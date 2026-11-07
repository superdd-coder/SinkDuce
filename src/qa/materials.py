"""List-owned material versions, parsed locally and searched within the list."""

import hashlib
import json
import re
import unicodedata
from heapq import nlargest
from pathlib import Path

from fastapi import HTTPException

from . import imports, store

MAX_SIZE = 20 * 1024 * 1024
MAX_CONTENT = 2_000_000
MAX_DOCUMENTS = 16
MAX_SOURCES = 16
MAX_QUERY_CHARS = 4000
MAX_QUERY_TERMS = 40
CHUNK_CHARS = 1050
CHUNK_OVERLAP = 100
CHUNKS_PER_DOCUMENT = 2
EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt", ".csv", ".tsv"}

_VERSION_FIELDS = (
    """m.*,v.id AS version_id,v.filename,v.parse_state,v.index_state,v.error_code,v.number"""
)


def _upload_filename(value):
    return unicodedata.normalize("NFC", Path(value.replace("\\", "/")).name)


def _version(conn, lid, mid, vid=None):
    row = conn.execute(
        f"""SELECT {_VERSION_FIELDS} FROM qa_materials m JOIN qa_material_versions v
        ON v.material_id=m.id AND v.id=coalesce(?,m.current_version_id)
        WHERE m.list_id=? AND m.id=?""",
        (vid, lid, mid),
    ).fetchone()
    if not row:
        store.fail("not_found", 404)
    return dict(row)


def version(collection, lid, mid, vid=None):
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        return _version(conn, lid, mid, vid)


def list_materials(collection, lid):
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        return [
            dict(r)
            for r in conn.execute(
                f"""SELECT {_VERSION_FIELDS}
            FROM qa_materials m JOIN qa_material_versions v ON v.id=m.current_version_id
            WHERE m.list_id=? ORDER BY m.created_at DESC,m.id""",
                (lid,),
            )
        ]


def upload(collection, lid, filename, raw, mid=None, expected_revision=None):
    filename = _upload_filename(filename)
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        store.fail("unsupported_material", 422)
    if len(raw) > MAX_SIZE:
        store.fail("parse_limit_exceeded", 413)
    if not raw:
        store.fail("empty_material", 422)
    # ZIP-based office files receive the same decompression bound as workbooks.
    if ext in {".docx", ".xlsx", ".pptx"}:
        from io import BytesIO
        from zipfile import BadZipFile, ZipFile

        try:
            with ZipFile(BytesIO(raw)) as z:
                if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                    store.fail("parse_limit_exceeded", 413)
        except BadZipFile:
            store.fail("unsupported_material", 422)
    digest = hashlib.sha256(raw).hexdigest()
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        listing = store.get_list(conn, lid)
        if listing["archived"]:
            store.fail("archived_list")
        if mid:
            old = _version(conn, lid, mid)
            if old["revision"] != expected_revision:
                store.fail("stale_revision")
        conn.create_function("qa_upload_filename", 1, _upload_filename, deterministic=True)
        exact = conn.execute(
            """SELECT m.id AS material_id,v.id AS version_id
            FROM qa_material_versions v JOIN qa_materials m ON m.id=v.material_id
            WHERE m.list_id=? AND v.content_hash=? AND qa_upload_filename(v.filename)=?
              AND (? IS NULL OR m.id=?)
            ORDER BY m.archived, v.id=m.current_version_id DESC,v.created_at DESC,v.id LIMIT 1""",
            (lid, digest, filename, mid, mid),
        ).fetchone()
        if exact:
            return _version(conn, lid, exact["material_id"], exact["version_id"])
        if not mid:
            names = conn.execute(
                """SELECT m.id FROM qa_materials m JOIN qa_material_versions v ON v.id=m.current_version_id
                WHERE m.list_id=? AND qa_upload_filename(v.filename)=? LIMIT 2""",
                (lid, filename),
            ).fetchall()
            if len(names) > 1:
                store.fail("ambiguous_material_name")
            if names:
                mid = names[0]["id"]
        vid = store.new_id()
        existing = bool(mid)
        if not existing:
            mid = store.new_id()
            conn.execute(
                "INSERT INTO qa_materials VALUES(?,?,?,1,0,?)", (mid, lid, vid, store.now())
            )
        number = conn.execute(
            "SELECT coalesce(max(number),0)+1 FROM qa_material_versions WHERE material_id=?", (mid,)
        ).fetchone()[0]
        path = imports.asset_path(collection, lid, "materials", vid)
        path.mkdir(parents=True, exist_ok=True)
        (path / ("blob" + ext)).write_bytes(raw)
        conn.execute(
            "INSERT INTO qa_material_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                vid,
                mid,
                number,
                filename,
                digest,
                "queued",
                "queued",
                "",
                "",
                "{}",
                store.now(),
            ),
        )
        if existing:
            conn.execute(
                "UPDATE qa_materials SET current_version_id=?,revision=revision+1 WHERE id=?",
                (vid, mid),
            )
        return _version(conn, lid, mid, vid)


def source(collection, lid, mid, vid):
    with store.connection(collection) as conn:
        row = conn.execute(
            """SELECT v.*,m.list_id FROM qa_material_versions v JOIN qa_materials m ON m.id=v.material_id
            WHERE m.list_id=? AND m.id=? AND v.id=?""",
            (lid, mid, vid),
        ).fetchone()
        if not row:
            store.fail("not_found", 404)
        path = imports.asset_path(collection, lid, "materials", vid) / (
            "blob" + Path(row["filename"]).suffix.lower()
        )
        if not path.is_file():
            store.fail("source_unavailable", 404)
        return dict(row), path


def queue_retry(collection, lid, mid, vid=None):
    """Expose queued state before starting the worker and invalidate older attempts."""
    with store.connection(collection, "update") as conn, conn:
        store.get_list(conn, lid)
        item = _version(conn, lid, mid, vid)
        conn.execute(
            """UPDATE qa_material_versions
            SET parse_state='queued',index_state='queued',error_code='',metadata_json='{}'
            WHERE id=? AND parse_state IN ('failed','processing')""",
            (item["version_id"],),
        )
        return _version(conn, lid, mid, item["version_id"])


def process(collection, lid, mid, vid):
    from src.parsers import parse_file

    try:
        item = version(collection, lid, mid, vid)
    except HTTPException as exc:
        if exc.status_code == 404:
            return  # A queued job can outlive its list.
        raise
    # An explicit retry can replace an interrupted job; only the latest claimed
    # attempt can write its result. Ready versions are never reparsed.
    attempt = json.dumps({"parse_attempt": store.new_id()})
    with store.connection(collection, "update") as conn, conn:
        claimed = conn.execute(
            """UPDATE qa_material_versions SET parse_state='processing',error_code='',metadata_json=?
            WHERE id=? AND parse_state='queued'""",
            (attempt, vid),
        ).rowcount
        if not claimed:
            return
    path = imports.asset_path(collection, lid, "materials", vid) / (
        "blob" + Path(item["filename"]).suffix.lower()
    )
    try:
        parsed = parse_file(path)
        if not parsed.content.strip() or len(parsed.content) > MAX_CONTENT:
            raise ValueError("empty_or_large_content")
        meta = {
            **parsed.metadata,
            "position_map": _page_positions(parsed.position_map, len(parsed.content)),
        }
        with store.connection(collection, "update") as conn, conn:
            conn.execute(
                """UPDATE qa_material_versions SET parse_state='ready',index_state='ready',
                content=?,metadata_json=? WHERE id=? AND metadata_json=?""",
                (parsed.content, json.dumps(meta, ensure_ascii=False, default=str), vid, attempt),
            )
    except Exception:  # noqa: BLE001 — provider/parser failures retain a manual or retry path
        with store.connection(collection, "update") as conn, conn:
            conn.execute(
                """UPDATE qa_material_versions SET parse_state='failed',index_state='failed',error_code='parse_failed'
                WHERE id=? AND metadata_json=?""",
                (vid, attempt),
            )


def archive(collection, lid, mid, expected_revision, archived):
    with store.connection(collection, "update") as conn, conn:
        store.get_list(conn, lid)
        updated = conn.execute(
            "UPDATE qa_materials SET archived=?,revision=revision+1 WHERE id=? AND list_id=? AND revision=?",
            (archived, mid, lid, expected_revision),
        ).rowcount
        if not updated:
            store.fail("stale_revision")
    return list_materials(collection, lid)


def ready_versions(collection, lid):
    return [
        m["current_version_id"]
        for m in list_materials(collection, lid)
        if not m["archived"] and m["parse_state"] == "ready"
    ]


def _resolve_rows(conn, lid, version_ids):
    ids = list(dict.fromkeys(version_ids))
    if len(ids) > MAX_DOCUMENTS:
        store.fail("parse_limit_exceeded", 422)
    rows = []
    for vid in ids:
        row = conn.execute(
            """SELECT m.id AS file_id,m.archived,v.id AS version_id,v.filename,v.number,v.parse_state
            FROM qa_material_versions v JOIN qa_materials m ON m.id=v.material_id
            WHERE m.list_id=? AND v.id=?""",
            (lid, vid),
        ).fetchone()
        if not row:
            store.fail("not_found", 404)
        if row["archived"]:
            store.fail("material_archived")
        if row["parse_state"] != "ready":
            store.fail("material_not_ready")
        rows.append(dict(row))
    return rows


def resolve_references(collection, lid, version_ids):
    """Resolve only exact ready versions of active materials in this list."""
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        rows = _resolve_rows(conn, lid, version_ids)
    return [
        {key: row[key] for key in ("file_id", "version_id", "filename", "number")} for row in rows
    ]


_HAN = "\u3400-\u9fff\uf900-\ufaff"
_TOKEN = re.compile(rf"[{_HAN}]+|[a-z0-9]+")
_STOP_WORDS = set(
    """a an and are as at be been but by can could did do does for from had has have how i if
    in is it its may me might my of on or our please provide say should that the their them
    then there these they this those to use using was we were what when where which who will
    with would you your available confirm describe document file information material question
    related reply tell answer based 请问 请确 确认 请提 提供 是否 什么 如何 可以 这个 相关 根据 文件 资料 问题 回复""".split()  # noqa: SIM905
)


def _tokens(value):
    for token in _TOKEN.findall(unicodedata.normalize("NFKC", value).casefold()):
        if "\u3400" <= token[0] <= "\ufaff":
            words = (token[i : i + 2] for i in range(len(token) - 1))
        else:
            words = (token,) if 2 <= len(token) <= 80 else ()
        yield from (word for word in words if word not in _STOP_WORDS)


def _name(value):
    return unicodedata.normalize("NFKC", Path(value.strip().replace("\\", "/")).name).casefold()


def _page_positions(entries, length):
    if not isinstance(entries, list):
        return []
    pages = {}
    for entry in entries[:20_000]:
        if not isinstance(entry, dict):
            continue
        offset, page = entry.get("char_offset"), entry.get("page_number")
        if type(offset) is int and 0 <= offset < length and type(page) is int and page > 0:
            pages[offset] = page
    return [{"char_offset": offset, "page_number": page} for offset, page in sorted(pages.items())]


def _chunks(body, metadata):
    try:
        meta = json.loads(metadata)
    except (TypeError, ValueError):
        meta = {}
    pages = _page_positions(meta.get("position_map"), len(body)) if isinstance(meta, dict) else []
    starts = [(item["char_offset"], item["page_number"]) for item in pages]
    if not starts or starts[0][0] != 0:
        starts.insert(0, (0, None))
    for i, (start, page) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
        while start < end:
            stop = min(start + CHUNK_CHARS, end)
            if stop < end:
                boundary = max(
                    body.rfind(sep, start + CHUNK_CHARS // 2, stop) for sep in ("\n", " ")
                )
                if boundary > start:
                    stop = boundary
            excerpt = body[start:stop].strip()
            if excerpt:
                yield start, page, excerpt
            if stop == end:
                break
            start = stop - CHUNK_OVERLAP


_CURRENT_DOCUMENTS = """SELECT m.id AS file_id,v.id AS version_id,v.filename,v.parse_state
FROM qa_materials m JOIN qa_material_versions v ON v.id=m.current_version_id
WHERE m.list_id=? AND m.archived=0"""


def _choose_documents(conn, lid, selected, names, allow_auto, terms):
    if not names:
        if selected:
            return selected, []
        if not allow_auto or not terms:
            return [], []
        return conn.execute(
            _CURRENT_DOCUMENTS + " AND v.parse_state='ready' ORDER BY v.created_at DESC,v.id",
            (lid,),
        ), []
    conn.create_function("qa_material_name", 1, _name, deterministic=True)
    conn.create_function(
        "qa_material_stem", 1, lambda name: Path(_name(name)).stem, deterministic=True
    )
    chosen, missing = {}, []
    for label in names:
        key = _name(label)
        if selected:
            rows = [item for item in selected if _name(item["filename"]) == key]
            if not rows:
                rows = [item for item in selected if Path(_name(item["filename"])).stem == key]
        else:
            rows = conn.execute(
                _CURRENT_DOCUMENTS + " AND qa_material_name(v.filename)=? LIMIT 2", (lid, key)
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    _CURRENT_DOCUMENTS + " AND qa_material_stem(v.filename)=? LIMIT 2", (lid, key)
                ).fetchall()
            if len(rows) != 1 or rows[0]["parse_state"] != "ready":
                rows = []
        if not rows:
            missing.append(label)
        for row in rows:
            chosen[row["version_id"]] = row
    return list(chosen.values()), missing


def _ranked_excerpts(content, metadata, terms, name_terms, *, explicit, check_cancel):
    """Rank excerpts by body matches, with filename matches breaking ties."""
    required = min(2, len(terms))
    name_hits = terms & name_terms
    supporting_hits = name_hits if explicit else set()
    direct = explicit and len(content) <= CHUNK_CHARS
    if not direct:
        if not required:
            return []
        # Substring hits are a cheap necessary condition before tokenizing each
        # excerpt. Filename matches cannot stand in for a hit in the body.
        normalized = unicodedata.normalize("NFKC", content).casefold()
        possible_hits = {term for term in terms if term in normalized}
        del normalized
        if not possible_hits or len(possible_hits | supporting_hits) < required:
            return []

    def candidates():
        for index, (start, page, excerpt) in enumerate(_chunks(content, metadata), 1):
            if check_cancel and index % 64 == 0:
                check_cancel()
            hits = terms.intersection(_tokens(excerpt)) if required else set()
            if hits and len(hits | supporting_hits) >= required:
                yield (len(hits), len(name_hits)), start, page, excerpt
            elif direct:
                yield (0, 0), start, page, excerpt

    return nlargest(CHUNKS_PER_DOCUMENT, candidates(), key=lambda item: (item[0], -item[1]))


def _source_candidates(conn, documents, terms, *, explicit, check_cancel):
    for item in documents:
        if check_cancel:
            check_cancel()
        vid = item["version_id"]
        doc = conn.execute(
            """SELECT substr(content,1,?) AS content,metadata_json
            FROM qa_material_versions WHERE id=?""",
            (MAX_CONTENT, vid),
        ).fetchone()
        if not doc or not doc["content"].strip():
            continue
        excerpts = _ranked_excerpts(
            doc["content"],
            doc["metadata_json"],
            terms,
            set(_tokens(Path(item["filename"]).stem)),
            explicit=explicit,
            check_cancel=check_cancel,
        )
        for index, (score, start, page, excerpt) in enumerate(excerpts):
            yield (
                score,
                index,
                {
                    "kind": "material",
                    "file_id": item["file_id"],
                    "version_id": vid,
                    "filename": item["filename"],
                    "chunk_id": f"{vid}:{start}",
                    "page_number": page,
                    "excerpt": excerpt,
                },
            )


def search_references(
    collection, lid, query, *, versions=(), names=(), allow_auto=False, check_cancel=None
):
    """Search list-owned text and keep only the strongest bounded set of excerpts."""
    names = list(
        dict.fromkeys(name.strip() for name in names if isinstance(name, str) and name.strip())
    )[:MAX_DOCUMENTS]
    if not (versions or names or allow_auto):
        return [], []
    terms = set(list(dict.fromkeys(_tokens(query[:MAX_QUERY_CHARS])))[:MAX_QUERY_TERMS])
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        selected = _resolve_rows(conn, lid, [item["version_id"] for item in versions])
        documents, missing = _choose_documents(conn, lid, selected, names, allow_auto, terms)
        explicit = bool(selected or names)
        # Inspect every eligible body but retain only the best excerpts. For
        # explicit selections, prefer one excerpt per file before adding seconds.
        ranked = nlargest(
            MAX_SOURCES,
            _source_candidates(
                conn, documents, terms, explicit=explicit, check_cancel=check_cancel
            ),
            key=(lambda item: (-item[1], item[0]))
            if explicit
            else (lambda item: (item[0][0], -item[1], item[0][1])),
        )
    return [source for _, _, source in ranked], missing
