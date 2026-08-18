from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from src.hot_words import store
from src.hot_words.import_util import (
    build_export_xlsx,
    build_template_csv,
    build_template_xlsx,
    parse_hot_words_file,
)

logger = logging.getLogger("hot_words")
router = APIRouter(prefix="/api/hot-words", tags=["hot-words"])


def _safe_filename(name: str, fallback: str = "hot-words") -> str:
    s = re.sub(r"[^\w\-.]+", "_", (name or "").strip(), flags=re.UNICODE)
    s = s.strip("._") or fallback
    return s[:80]


def _public_lib(lib) -> dict:
    pins = store.get_pinned_library_ids()
    data = lib.model_dump()
    data.update(store.library_summary(lib, pinned_ids=pins))
    return data


@router.get("")
async def list_libraries():
    pins = store.get_pinned_library_ids()
    return [store.library_summary(lib, pinned_ids=pins) for lib in store.list_libraries()]


@router.get("/template.csv")
async def download_template_csv():
    data = build_template_csv()
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="hot-words-template.csv"',
        },
    )


@router.get("/template.xlsx")
async def download_template_xlsx():
    data = build_template_xlsx()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="hot-words-template.xlsx"',
        },
    )


@router.get("/default")
async def get_default_library():
    pins = store.get_pinned_library_ids()
    return {
        "default_library_id": store.get_default_library_id(),
        "pinned_library_ids": pins,
    }


@router.put("/default")
async def set_default_library(body: dict = Body()):
    """Pin a library (compat). Prefer PUT /hot-words/pins."""
    raw = body.get("library_id", body.get("default_library_id"))
    if raw is None or raw == "" or raw is False:
        store.set_default_library_id(None)
        return {
            "default_library_id": store.get_default_library_id(),
            "pinned_library_ids": store.get_pinned_library_ids(),
        }
    try:
        default_id = store.set_default_library_id(str(raw).strip())
    except FileNotFoundError:
        raise HTTPException(404, "Hot words library not found")
    return {
        "default_library_id": default_id,
        "pinned_library_ids": store.get_pinned_library_ids(),
    }


@router.get("/pins")
async def get_pins():
    return {"pinned_library_ids": store.get_pinned_library_ids()}


@router.put("/pins")
async def set_pins(body: dict = Body()):
    raw = body.get("library_ids", body.get("pinned_library_ids"))
    if raw is None:
        raise HTTPException(400, "library_ids is required")
    if not isinstance(raw, list):
        raise HTTPException(400, "library_ids must be a list")
    try:
        pins = store.set_pinned_library_ids([str(x) for x in raw])
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"pinned_library_ids": pins}


@router.post("/import")
async def import_library(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    description: str | None = Form(None),
):
    """Create a library from an uploaded CSV or Excel (.xlsx) file."""
    filename = file.filename or "import.csv"
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")

    try:
        words, name_from_file, desc_from_file = parse_hot_words_file(filename, raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Hot words import parse failed: %s", e)
        raise HTTPException(400, f"Failed to parse file: {e}") from e

    if not words:
        raise HTTPException(
            400,
            "No valid words found. Need a header row with text,weight,lang "
            "and at least one non-empty text cell.",
        )

    stem = Path(filename).stem.strip() or "Imported library"
    lib_name = (name or "").strip() or name_from_file or stem
    lib_desc = (description or "").strip() or desc_from_file or ""

    lib = store.create_library(name=lib_name, description=lib_desc, words=words)
    logger.info(
        "Imported hot words library id=%s name=%s words=%d file=%s",
        lib.id,
        lib.name,
        len(words),
        filename,
    )
    return _public_lib(lib)


@router.get("/{library_id}/export.xlsx")
async def export_library_xlsx(library_id: str):
    lib = store.get_library(library_id)
    if lib is None:
        raise HTTPException(404, "Hot words library not found")
    data = build_export_xlsx(lib.words, name=lib.name, description=lib.description)
    fname = _safe_filename(lib.name or "hot-words") + ".xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
        },
    )


@router.get("/{library_id}")
async def get_library(library_id: str):
    lib = store.get_library(library_id)
    if lib is None:
        raise HTTPException(404, "Hot words library not found")
    return _public_lib(lib)


@router.post("")
async def create_library(body: dict = Body()):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    description = body.get("description", "")
    words = body.get("words")
    lib = store.create_library(name=name, description=description, words=words)
    return _public_lib(lib)


@router.put("/{library_id}")
async def update_library(library_id: str, body: dict = Body()):
    try:
        lib = store.update_library(library_id, **body)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(404, "Hot words library not found")
    return _public_lib(lib)


@router.delete("/{library_id}")
async def delete_library(library_id: str):
    try:
        deleted = store.delete_library(library_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Hot words library not found")
    return {"message": "Hot words library deleted"}
