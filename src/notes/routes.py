"""Notes API routes — CRUD, content, distillation, propagation, and ingestion."""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from qdrant_client.models import FieldCondition, Filter, MatchValue

from src.notes import store
from src.notes.service import distill_note, propagate_forward, parse_injection_blocks
from src.services import services
from src.rag.markdown_chunker import MarkdownChunker, MarkdownParentChildChunker
from src.rag.collection_utils import get_collection_embedding
from src.tasks.handlers import (
    _enrich_lock,
    _do_enrich,
    _build_enriched_text,
)

logger = logging.getLogger("notes")
router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────


def _get_ingested_note_ids(collection: str) -> set[str]:
    """Build a set of note IDs that have chunks ingested in Qdrant."""
    ingested: set[str] = set()
    try:
        if not services.db.collection_exists(collection):
            return ingested
        filter_cond = Filter(
            must=[FieldCondition(key="file_type", match=MatchValue(value="note"))]
        )
        offset = None
        while True:
            points, offset = services.db.scroll_points(
                collection=collection,
                limit=200,
                offset=offset,
                scroll_filter=filter_cond,
                with_payload=["source"],
                with_vectors=False,
            )
            for p in points:
                source = p.get("payload", {}).get("source", "")
                if isinstance(source, str) and source.startswith("__note__:"):
                    ingested.add(source[len("__note__:"):])
            if offset is None:
                break
    except Exception as e:
        logger.warning("Failed to query ingested note IDs for collection %s: %s", collection, e)
    return ingested


def _do_ingest_note(collection: str, note_id: str, note_title: str, content: str):
    """Run full ingest pipeline: chunk → enrich → embed → store.

    Runs in a background thread via BackgroundTasks. Reuses the
    enrichment/embedding locks from handlers.py.
    """
    t_start = time.time()

    # Ensure collection exists in Qdrant
    if not services.db.collection_exists(collection):
        vector_size = services.embedding.dimensions if services.embedding else 1024
        services.db.create_collection(collection, vector_size=vector_size)

    config = services.db.get_collection_config(collection)

    # ── Stage 1: Chunking ──
    source = f"__note__:{note_id}"
    file_id = uuid.uuid4().hex
    extra_meta = {
        "file_type": "note",
        "note_id": note_id,
        "note_title": note_title,
        "file_id": file_id,
        "ingested_at": time.time(),
        "source_label": f"Note: {note_title}",
    }

    # Write snapshot to collections/{id}/files/{file_id}/
    from src.collections.file_index import ensure_files_dir
    import re as _re
    safe_title = _re.sub(r'[^\w一-鿿\s-]', '', note_title).strip()[:80] or note_id
    safe_title = _re.sub(r'\s+', '_', safe_title)
    snapshot_dir = ensure_files_dir(collection, file_id)
    (snapshot_dir / f"{safe_title}.md").write_text(content, encoding="utf-8")

    if config.get("chunk_mode") == "parent_child":
        chunker = MarkdownParentChildChunker(
            parent_strategy=config.get("parent_strategy", "heading"),
            parent_chunk_size=config.get("parent_chunk_size", 1024),
            parent_overlap=config.get("parent_chunk_overlap", 128),
            parent_buffer_ratio=config.get("buffer_ratio", 0.5),
            child_chunk_size=config.get("child_chunk_size", 128),
            child_overlap=config.get("child_chunk_overlap", 32),
            child_buffer_ratio=config.get("buffer_ratio", 0.5),
        )
    else:
        chunker = MarkdownChunker(
            max_tokens=config.get("chunk_size", 512),
            buffer_ratio=config.get("buffer_ratio", 0.5),
            chunk_overlap=config.get("chunk_overlap", 64),
        )

    chunks = chunker.chunk_with_metadata(
        content, source=source, extra_metadata=extra_meta
    )
    logger.info("[INGEST] Note %s: chunked into %d chunks (%.1fs)",
                note_id, len(chunks), time.time() - t_start)

    if not chunks:
        logger.warning("[INGEST] Note %s: chunking produced 0 chunks, aborting", note_id)
        return

    # ── Stage 2: Enrichment (serialized, non-fatal) ──
    t_ctx = time.time()
    contextual_enabled = config.get("contextual_enabled", True)
    if contextual_enabled:
        _Doc = type("_Doc", (), {"content": content})
        _enrich_lock.acquire()
        try:
            chunks = _do_enrich(chunks, _Doc, config)
            logger.info("[INGEST] Note %s: enrichment done (%.1fs)",
                        note_id, time.time() - t_ctx)
        except Exception as e:
            logger.warning("[INGEST] Note %s: enrichment failed (%.1fs), continuing: %s",
                        note_id, time.time() - t_ctx, e)
        finally:
            _enrich_lock.release()

    # ── Stage 3: Embedding ──
    t_emb = time.time()
    embedding = get_collection_embedding(config, collection)
    texts = [_build_enriched_text(c) for c in chunks]
    embeddings = embedding.embed_texts(texts)
    logger.info("[INGEST] Note %s: embedding done (%.1fs)",
                note_id, time.time() - t_emb)

    # ── Stage 4: Storage ──
    ids = []
    for c in chunks:
        if c.chunk_type in ("parent", "child"):
            ids.append(c.metadata["chunk_id"])
        else:
            new_id = str(uuid.uuid4())
            c.metadata["chunk_id"] = new_id
            ids.append(new_id)

    payloads = []
    for c in chunks:
        payload = {
            "text": c.text,
            "parent_id": c.parent_id,
            "chunk_type": c.chunk_type,
        }
        if c.metadata.get("context"):
            payload["context"] = c.metadata["context"]
        if c.metadata.get("summary"):
            payload["summary"] = c.metadata.get("summary")
        payload.update({k: v for k, v in c.metadata.items()
                        if k not in ("context", "summary")})
        payload["collection"] = collection
        payloads.append(payload)

    t_store = time.time()
    services.db.upsert_points(
        collection=collection, ids=ids, vectors=embeddings,
        payloads=payloads,
    )

    # ── Sparse encoding ──
    try:
        from src.rag.sparse_encoder import SparseEncoder
        encoder = SparseEncoder()
        encoder.load(services.db, collection)
        texts = [_build_enriched_text(c) for c in chunks]
        sparse_vectors = encoder.encode(texts)
        encoder.save(services.db, collection)
        services.db.upsert_sparse_vectors(
            collection=collection, ids=ids, sparse_vectors=sparse_vectors,
        )
    except Exception:
        logger.warning("[INGEST] Note %s: sparse encoding failed", note_id, exc_info=True)

    # Update file index
    try:
        from src.collections.file_index import add as add_file_index
        add_file_index(collection, file_id, source, f"Note: {note_title}", "note", len(chunks))
    except Exception:
        logger.warning("[INGEST] Note %s: failed to update files.json", note_id, exc_info=True)

    logger.info("[INGEST] Note %s: store done in %.1fs. Total: %.1fs",
                note_id, time.time() - t_store, time.time() - t_start)

    # Clean up old re-ingest snapshots (remove all except current file_id for this source)
    try:
        from src.collections.file_index import load as load_file_index, save as save_file_index
        idx = load_file_index(collection)
        for fid, entry in list(idx.items()):
            if entry.get("source") == source and fid != file_id:
                old_dir = snapshot_dir.parent / fid
                if old_dir.exists():
                    shutil.rmtree(old_dir)
                del idx[fid]
        if any(entry.get("source") == source and fid != file_id for fid, entry in idx.items()):
            save_file_index(collection, idx)
    except Exception:
        pass


# ── Notes CRUD ─────────────────────────────────────────────────


@router.get("/notes/{collection}")
async def list_notes(collection: str):
    """List all notes for a collection, sorted by updated_at descending."""
    notes = store.list_notes(collection)
    ingested_ids = _get_ingested_note_ids(collection)
    items = []
    for note in notes:
        referenced_by = store.get_referenced_by(note.id)
        items.append({
            "id": note.id,
            "title": note.title,
            "collection": note.collection,
            "created_at": note.created_at.isoformat(),
            "updated_at": note.updated_at.isoformat(),
            "is_extracted": len(referenced_by) > 0,
            "extracted_into": referenced_by,
            "is_ingested": note.id in ingested_ids,
        })
    return {"collection": collection, "notes": items}


@router.post("/notes/{collection}")
async def create_note(collection: str, body: dict = Body()):
    """Create a new note in a collection."""
    title = body.get("title") or datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info("[CREATE] Note '%s' in collection '%s'", title, collection)
    note = store.create_note(collection, title)
    return {
        "id": note.id,
        "title": note.title,
        "collection": note.collection,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


@router.get("/notes/{collection}/{note_id}")
async def get_note(collection: str, note_id: str):
    """Get note metadata, content, and references."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    content = store.get_content(note_id) or ""
    references = store.get_references(note_id)
    referenced_by = store.get_referenced_by(note_id)

    # Check if this note is ingested into Qdrant
    is_ingested = False
    try:
        if services.db.collection_exists(collection):
            filter_cond = Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=f"__note__:{note_id}"))]
            )
            is_ingested = services.db.count_by_filter(collection, filter_cond) > 0
    except Exception:
        pass

    # Enrich references with source titles
    for ref in references:
        source = store.get_note(ref.get("source_note_id", ""))
        ref["source_title"] = source.title if source else ref.get("source_note_id", "")
    return {
        "id": note.id,
        "title": note.title,
        "collection": note.collection,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
        "content": content,
        "references": references,
        "is_extracted": len(referenced_by) > 0,
        "extracted_into": referenced_by,
        "is_ingested": is_ingested,
    }


@router.put("/notes/{collection}/{note_id}")
async def update_note(collection: str, note_id: str, body: dict = Body()):
    """Update note content and/or title. Auto-syncs injection block references."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    # Update title if provided
    if "title" in body:
        store.update_note(note_id, title=body["title"])

    # Update content if provided
    if "content" in body:
        content = body["content"]

        # Sync references — re-parse injection blocks from content
        # IMPORTANT: get old refs BEFORE saving new content
        old_refs = store.get_references(note_id)
        old_source_ids = {r["source_note_id"] for r in old_refs}

        blocks = parse_injection_blocks(content)
        refs = []
        new_source_ids: set[str] = set()
        for block in blocks:
            source_id = block["source_note_id"]
            new_source_ids.add(source_id)
            source = store.get_note(source_id)
            refs.append({
                "block_id": block["block_id"],
                "source_note_id": source_id,
                "source_title": source.title if source else "",
            })

        # Save the content and references
        store.save_content(note_id, content)
        store.save_references(note_id, refs)

        # Update referenced_by: diff old vs new sources
        for removed_source_id in old_source_ids - new_source_ids:
            store._remove_referenced_by(removed_source_id, note_id)
        for added_source_id in new_source_ids - old_source_ids:
            store._add_referenced_by(added_source_id, note_id)

    return {"message": "Note updated", "id": note_id}


@router.delete("/notes/{collection}/{note_id}")
async def delete_note(collection: str, note_id: str):
    """Delete a note and clean up all references and ingested chunks."""
    logger.info("[DELETE] Note %s in collection '%s'", note_id, collection)

    # Clean up ingested chunks from Qdrant
    source = f"__note__:{note_id}"
    try:
        if services.db.collection_exists(collection):
            services.db.delete_by_filter(collection, key="source", value=source)
            logger.info("[DELETE] Cleaned up ingested chunks for note %s", note_id)
    except Exception as e:
        logger.warning("Failed to clean up ingested chunks for deleted note %s: %s", note_id, e)

    deleted = store.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return {"message": "Note deleted"}


# ── Distillation ───────────────────────────────────────────────


@router.post("/notes/{collection}/{note_id}/distill")
async def distill_into_note(collection: str, note_id: str, body: dict = Body()):
    """Generate (or return cached) distillation of source_note for target note.
    The frontend is responsible for inserting the block into the content."""
    source_note_id = body.get("source_note_id")
    if not source_note_id:
        raise HTTPException(status_code=400, detail="source_note_id is required")

    source = store.get_note(source_note_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source note {source_note_id} not found")

    target = store.get_note(note_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"Target note {note_id} not found")

    logger.info("[DISTILL] %s → %s in collection '%s'", source_note_id, note_id, collection)

    # Generate distilled content (uses cache if available).
    # Run in thread pool — the LLM call is synchronous and blocks the
    # event loop, causing all other requests (getNote etc.) to queue.
    distilled = await asyncio.to_thread(distill_note, source_note_id, note_id)

    block_id = uuid.uuid4().hex[:12]

    return {
        "message": "Distillation ready",
        "block_id": block_id,
        "source_note_id": source_note_id,
        "source_title": source.title,
        "distilled_content": distilled,
    }


# ── Propagation ────────────────────────────────────────────────


@router.get("/notes/{collection}/{note_id}/propagation-preview")
async def get_propagation_preview(collection: str, note_id: str):
    """Preview the full propagation chain if this note's content changes."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    links = store.build_propagation_chain(note_id)
    return {
        "origin_id": note_id,
        "origin_title": note.title,
        "links": links,
        "total_affected": len(links),
    }


@router.post("/notes/{collection}/{note_id}/propagate")
async def trigger_propagation(collection: str, note_id: str, background_tasks: BackgroundTasks):
    """Trigger backward propagation: re-distill this note into all notes that reference it.
    Chain propagation (downstream) is automatic and doesn't require user confirmation.
    Runs in background to avoid blocking."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    # Run propagation in background to avoid blocking the response
    def run_propagation():
        logger.info("[PROPAGATE] Starting propagation from note %s in '%s'", note_id, collection)
        updated = propagate_forward(note_id, auto=True)
        logger.info("[PROPAGATE] Updated %d notes: %s", len(updated), updated)

    background_tasks.add_task(run_propagation)

    return {
        "message": "Propagation started in background",
        "status": "started",
    }


# ── Ingestion ──────────────────────────────────────────────────


@router.post("/notes/{collection}/{note_id}/ingest")
async def ingest_note(collection: str, note_id: str, background_tasks: BackgroundTasks):
    """Ingest a note's markdown content into the Qdrant vector store
    so it becomes searchable via RAG."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    content = store.get_content(note_id) or ""
    if not content.strip():
        raise HTTPException(status_code=400, detail="Note content is empty")

    # Remove any existing ingestion first (allows re-ingestion after edits)
    source = f"__note__:{note_id}"
    try:
        if services.db.collection_exists(collection):
            services.db.delete_by_filter(collection, key="source", value=source)
    except Exception:
        pass

    def run_ingestion():
        logger.info("[INGEST] Starting ingestion for note %s in collection '%s'", note_id, collection)
        try:
            _do_ingest_note(collection, note_id, note.title, content)
            logger.info("[INGEST] Completed ingestion for note %s", note_id)
        except Exception as e:
            logger.error("[INGEST] Failed to ingest note %s: %s", note_id, e, exc_info=True)

    background_tasks.add_task(run_ingestion)

    return {"message": "Ingestion started", "status": "pending"}


@router.delete("/notes/{collection}/{note_id}/ingest")
async def remove_note_ingestion(collection: str, note_id: str):
    """Remove all ingested chunks for a note from Qdrant."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    source = f"__note__:{note_id}"
    logger.info("[INGEST] Removing ingestion for note %s source=%s from collection '%s'", note_id, source, collection)

    if not services.db.collection_exists(collection):
        logger.warning("[INGEST] Collection '%s' does not exist, nothing to remove", collection)
        return {"message": "Collection not found, nothing to remove", "is_ingested": False}

    try:
        services.db.delete_by_filter(collection, key="source", value=source)
        logger.info("[INGEST] Removed ingestion for note %s from collection '%s'", note_id, collection)
    except Exception as e:
        logger.error("[INGEST] Failed to remove note %s: %s", note_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to remove ingestion: {e}")

    # Update file index
    try:
        from src.collections.file_index import remove_by_source as remove_file_index
        remove_file_index(collection, source)
    except Exception:
        pass

    return {"message": "Ingestion removed", "is_ingested": False}


# ── Image upload & serve ──────────────────────────────────────

IMAGES_DIR = Path("data").resolve() / "notes"


@router.post("/notes/{collection}/{note_id}/images")
async def upload_note_image(collection: str, note_id: str, file: UploadFile = File(...)):
    """Upload an image for a note. Returns the URL path to use in markdown."""
    note = store.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    content_bytes = await file.read()
    filename = file.filename or "image.png"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    safe_name = f"{uuid.uuid4().hex[:10]}.{ext}"

    images_dir = IMAGES_DIR / collection / note_id / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    image_path = images_dir / safe_name
    image_path.write_bytes(content_bytes)

    url = f"/api/notes/{collection}/{note_id}/images/{safe_name}"
    logger.info("[IMAGE] Uploaded %s (%d bytes) for note %s", safe_name, len(content_bytes), note_id)
    return {"url": url, "filename": safe_name}


@router.get("/notes/{collection}/{note_id}/images/{filename}")
async def serve_note_image(collection: str, note_id: str, filename: str):
    """Serve an uploaded image for a note."""
    image_path = IMAGES_DIR / collection / note_id / "images" / filename
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(image_path))
