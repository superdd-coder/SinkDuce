"""Read-only, model-free workspace search.

Metadata and ACLs are checked on every request. Small text artifacts are cached
by mtime with a byte budget; large documents are searched in bounded chunks.
No vector service, migrations, or generated-content writes happen here.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import json
import logging
import re
import sqlite3
import shutil
import tempfile
import threading
import unicodedata

from src.collections import store as collections
from src.identity import get_actor
from src.identity.policy import can, may_see_everything
from src.identity.roles import get_role
from src.identity.visibility import filter_collection_ids

CATEGORIES = ("projects", "files", "meetings", "minutes", "notes")
logger = logging.getLogger(__name__)
_CACHE_LIMIT = 32 * 1024 * 1024
_TEXT_LIMIT = 2 * 1024 * 1024
_cache: OrderedDict[str, tuple[tuple[int, int], str, int]] = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def terms_for(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize(query).split()))


def plain(value: str) -> str:
    value = re.sub(r"^\s*(?::::image|:::|(?:image_id|file_id):[^\n]*)\s*$", " ", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*(?:ocr_text|description):[ \t]*", "", value, flags=re.MULTILINE)
    value = re.sub(r"\[(?:spk:\d+|stt_[^\]]+)\]", " ", value)
    value = re.sub(r"</?[A-Za-z][^>]*>", " ", value)
    value = re.sub(r"!?\[([^\]]+)\]\([^\n)]*\)", r"\1", value)
    value = re.sub(r"^\s*(?:#{1,6}[ \t]+|```[\w-]*)", "", value, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", value.replace("`", "").replace("**", "")).strip()


def excerpt(value: str, terms: tuple[str, ...]) -> str:
    text = plain(value)
    folded = normalize(text)
    positions = [folded.find(term) for term in terms if term in folded]
    start = max(0, min(positions, default=0) - 65)
    end = min(len(text), start + 230)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _safe_child(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Search artifact is outside its resource directory")
    return path


def _small_text(path: Path, stat) -> str:
    global _cache_bytes
    key = str(path)
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        old = _cache.get(key)
        if old and old[0] == stamp:
            _cache.move_to_end(key)
            return old[1]
    text = path.read_text(encoding="utf-8", errors="replace")
    cost = len(text) * 4
    with _cache_lock:
        old = _cache.pop(key, None)
        if old:
            _cache_bytes -= old[2]
        while _cache and _cache_bytes + cost > _CACHE_LIMIT:
            _cache_bytes -= _cache.popitem(last=False)[1][2]
        if cost <= _CACHE_LIMIT:
            _cache[key] = (stamp, text, cost)
            _cache_bytes += cost
    return text


def match_text(title: str, text: str, terms: tuple[str, ...]) -> tuple[int, str] | None:
    name = normalize(title)
    body = normalize(text)
    if not all(term in name or term in body for term in terms):
        return None
    title_hits = sum(term in name for term in terms)
    score = title_hits * 10 + (10 if name.startswith(" ".join(terms)) else 0)
    # A filename/title match needs only its location; reserve excerpts for body
    # hits so a list of file names doesn't turn into a wall of unrelated text.
    return score, "" if title_hits == len(terms) else excerpt(text, terms)


def match_path(title: str, path: Path | None, terms: tuple[str, ...]) -> tuple[int, str] | None:
    name = normalize(title)
    if all(term in name for term in terms):
        return match_text(title, "", terms)
    if path is None or not path.is_file():
        return match_text(title, "", terms)
    stat = path.stat()
    if stat.st_size <= _TEXT_LIMIT:
        return match_text(title, _small_text(path, stat), terms)
    # Large documents: remember matching terms and one excerpt, not the document.
    found = {term for term in terms if term in name}
    snippet = ""
    overlap = ""
    with path.open(encoding="utf-8", errors="replace") as stream:
        while chunk := stream.read(65536):
            part = overlap + chunk
            folded = normalize(part)
            hits = {term for term in terms if term in folded}
            found.update(hits)
            if hits and not snippet:
                snippet = excerpt(part, terms)
            if len(found) == len(terms):
                break
            overlap = part[-max(256, max(map(len, terms)) * 2):]
    if len(found) != len(terms):
        return None
    score = sum(term in name for term in terms) * 10 + (10 if name.startswith(" ".join(terms)) else 0)
    return score, snippet


@dataclass
class Document:
    category: str
    id: str
    title: str
    context: str = ""
    collection_id: str | None = None
    file_id: str | None = None
    meeting_id: str | None = None
    section_id: str | None = None
    note_id: str | None = None
    text: str = ""
    path: Path | None = None

    def result(self, match: tuple[int, str]) -> dict:
        return {key: value for key, value in vars(self).items() if key not in {"text", "path"} and value is not None} | {
            "score": match[0], "snippet": match[1],
        }


def _folder_context(folder_id: str | None, folders: dict) -> str | None:
    names, seen = [], set()
    while folder_id:
        if folder_id in seen or folder_id not in folders:
            return None
        seen.add(folder_id)
        folder = folders[folder_id]
        if folder.get("archived"):
            return None
        names.append(folder["name"])
        folder_id = folder.get("parent_folder_id")
    return " / ".join(reversed(names))


def _read_index(db: Path):
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        # Keep all tables on one SQLite read snapshot while uploads/renames run.
        conn.execute("BEGIN")
        folders = {r["folder_id"]: dict(r) for r in conn.execute("SELECT * FROM folders")}
        mounts: dict[str, list] = {}
        for row in conn.execute("SELECT * FROM file_paths ORDER BY is_primary DESC, path_id"):
            mounts.setdefault(row["file_id"], []).append(dict(row))
        rows = conn.execute("""SELECT f.*, v.storage_file_id, v.archived AS version_archived
            FROM files f JOIN file_versions v ON v.version_id=f.current_version_id
            WHERE COALESCE(f.archived,0)=0 AND COALESCE(v.archived,0)=0""").fetchall()
        return folders, mounts, rows
    finally:
        conn.close()


def _read_index_copy(db: Path):
    """WAL databases on read-only volumes may lack a writable shared-memory file.

    Take a stable DB + WAL copy into a disposable directory in that case. Never
    use immutable=1 on the original: that silently ignores uncheckpointed data.
    Check both file stamps around the copy to reject concurrent checkpoints.
    """
    paths = (db, db.with_name(db.name + "-wal"))
    def stamps():
        return [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in paths]
    with tempfile.TemporaryDirectory(prefix="sinkduce-search-") as temp:
        target = Path(temp) / db.name
        for _ in range(3):
            before = stamps()
            for path, stamp in zip(paths, before):
                dest = Path(temp) / path.name
                if stamp is not None:
                    shutil.copyfile(path, dest)
                else:
                    dest.unlink(missing_ok=True)
            if before == stamps():
                return _read_index(target)
        raise OSError("File index changed while taking search snapshot")


def _file_documents(meta: dict):
    cid = meta["id"]
    root = _safe_child(collections.COLLECTIONS_DIR, cid)
    db = root / "meta.db"
    if not db.is_file():
        # Legacy libraries without SQLite still have their display index.
        index_path = root / "files.json"
        if not index_path.is_file():
            return
        for fid, row in json.loads(index_path.read_text()).items():
            if row.get("archived") or str(row.get("source", "")).startswith(("__note__:", "__meeting__:")):
                continue
            yield Document("files", f"{cid}/{fid}", row.get("source_label") or fid,
                           meta["name"], collection_id=cid, file_id=fid,
                           path=_safe_child(root, "files", fid, "parsed.txt"))
        return
    # Open read-only: search must not create a DB or run lazy migrations.
    try:
        folders, mounts, rows = _read_index(db)
    except sqlite3.OperationalError as error:
        # Extended SQLite codes retain the base code in their low byte.
        if (getattr(error, "sqlite_errorcode", 0) & 0xff) not in {sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY}:
            raise
        folders, mounts, rows = _read_index_copy(db)
    for row in rows:
        row = dict(row)
        if str(row.get("source") or "").startswith(("__note__:", "__meeting__:")):
            continue
        fid = row["file_id"]
        paths = mounts.get(fid, [])
        visible_paths = [_folder_context(p.get("folder_id"), folders) for p in paths if not p.get("archived")]
        visible_paths = [p for p in visible_paths if p is not None]
        if paths and not visible_paths:
            continue
        froot = _safe_child(root, "files", fid)
        vroot = _safe_child(froot, row["current_version_id"])
        parsed = vroot / "parsed.txt"
        # Flat storage is a legacy fallback only; never search an older version.
        if not vroot.is_dir():
            parsed = froot / "parsed.txt"
        name = Path(str(row.get("storage_file_id") or "").replace("\\", "/")).name
        context = " / ".join(p for p in [meta["name"], visible_paths[0] if visible_paths else ""] if p)
        yield Document("files", f"{cid}/{fid}", name or row.get("source_label") or fid,
                       context, collection_id=cid, file_id=fid, path=parsed)


def _meeting_documents(archived_collections: set[str], wanted):
    from src.meeting import store
    for meeting in store.list_meetings():
        if meeting.archived or (meeting.allocated_collections and set(meeting.allocated_collections) <= archived_collections):
            continue
        date = meeting.created_at.date().isoformat()
        yield Document("meetings", meeting.id, meeting.title or meeting.id, date, meeting_id=meeting.id)
        if "minutes" not in wanted:
            continue
        sections = {"tab_general": {"title": "", "tab_id": "tab_general"}}
        sections.update({tab["tab_id"]: tab for tab in (meeting.tabs or []) if tab.get("tab_id")})
        for sid, tab in sections.items():
            if not re.fullmatch(r"[\w-]+", sid):
                continue
            content = store.get_section_md(meeting.id, sid)
            if sid == "tab_general" and not content:
                content = meeting.summary
            if not content:
                continue
            yield Document("minutes", f"{meeting.id}/{sid}", tab.get("title") or meeting.title or meeting.id,
                           meeting.title if tab.get("title") else date, meeting_id=meeting.id, section_id=sid,
                           text=content)


def _note_documents(visible: dict):
    from src.notes import store
    for note in store.list_notes():
        if note.collection not in visible:
            continue
        yield Document("notes", note.id, note.title or note.id, visible[note.collection]["name"],
                       collection_id=note.collection, note_id=note.id, text=store.get_content(note.id) or "")


def search_workspace(query: str, category: str = "all", limit: int = 8, offset: int = 0) -> dict:
    terms = terms_for(query)
    wanted = CATEGORIES if category == "all" else (category,)
    matches: dict[str, list] = {key: [] for key in wanted}
    unavailable = set()
    if terms:
        actor = get_actor()
        role = get_role(actor.role_id)
        library_allowed = may_see_everything(actor) or bool(role and role.allows_module("database"))
        # Metadata failure must never broaden visibility or expose orphan content.
        try:
            metas = collections.list_collections_meta()
        except (OSError, ValueError):
            logger.exception("Workspace search metadata unavailable")
            return {"query": query.strip(), "groups": [], "unavailable_categories": list(wanted)}
        archived = {m["id"] for m in metas if m.get("archived")}
        visible_ids = set(filter_collection_ids(actor, [m["id"] for m in metas if not m.get("archived") and not m["id"].startswith("__")])) if library_allowed else set()
        visible = {m["id"]: m for m in metas if m["id"] in visible_ids}

        def collect(documents, affected):
            try:
                for doc in documents:
                    if doc.category not in wanted:
                        continue
                    match = match_path(doc.title, doc.path, terms) if doc.path else match_text(doc.title, doc.text, terms)
                    if match is not None:
                        matches[doc.category].append(doc.result(match))
            except (OSError, ValueError, sqlite3.DatabaseError):
                logger.exception("Workspace search source unavailable")
                unavailable.update(set(affected).intersection(wanted))

        if library_allowed:
            if "projects" in wanted:
                collect((Document("projects", m["id"], m["name"], collection_id=m["id"]) for m in visible.values()), ["projects"])
            if "files" in wanted:
                for meta in visible.values():
                    collect(_file_documents(meta), ["files"])
            if "notes" in wanted:
                collect(_note_documents(visible), ["notes"])
        if can(actor, "meeting.read", {}) and set(wanted).intersection({"meetings", "minutes"}):
            collect(_meeting_documents(archived, wanted), ["meetings", "minutes"])
    groups = []
    for key, items in matches.items():
        items.sort(key=lambda item: (-item["score"], normalize(item["title"]), item["id"]))
        groups.append({"category": key, "total": len(items), "items": items[offset:offset + limit],
                       "has_more": len(items) > offset + limit, "next_offset": offset + min(limit, max(0, len(items) - offset))})
    return {"query": query.strip(), "groups": groups, "unavailable_categories": sorted(unavailable)}
