from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path

from src.chat_attachments.models import MAX_ATTACHMENTS, MAX_FILE_BYTES, Attachment

_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class AttachmentStore:
    def __init__(self, root: Path, session_owner=None):
        self.root = Path(root)
        self._session_owner = session_owner

    @cached_property
    def resources(self):
        from src.chat_attachments.limits import ResourceLedger
        return ResourceLedger(self.root, self._session_owner)

    def upload_path(self, lease) -> Path:
        return self.resources.upload_path(lease)

    def _dir(self, session_id: str, attachment_id: str = "") -> Path:
        if not _ID.fullmatch(session_id) or (attachment_id and not _ID.fullmatch(attachment_id)):
            raise ValueError("Invalid attachment or session ID")
        return self.root / session_id / attachment_id

    def _write(self, a: Attachment) -> None:
        path = self._dir(a.session_id, a.id) / "metadata.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(a.model_dump_json(), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def validate_filename(filename: str) -> str:
        filename = Path(filename.replace("\\", "/")).name
        if not filename or filename in {".", ".."} or len(filename) > 240 or any(ord(c) < 32 for c in filename):
            raise ValueError("Invalid filename")
        return filename

    def create(self, session_id: str, filename: str, data: bytes) -> Attachment:
        filename = self.validate_filename(filename)
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("File must contain between 1 byte and 50 MiB")
        owner = self._session_owner(session_id) if self._session_owner else None
        lease = self.resources.reserve(session_id, owner or "local", len(data), parse=False)
        try:
            self.upload_path(lease).write_bytes(data)
            return self.commit_upload(lease, filename, len(data))
        finally:
            self.resources.abort(lease)

    def commit_upload(self, lease, filename: str, size: int, *, check=None) -> Attachment:
        filename = self.validate_filename(filename)
        a = Attachment(id=lease.id, session_id=lease.session_id, filename=filename,
                       size=size, created_at=datetime.now(UTC).isoformat())
        with _LOCK:
            d = self._dir(a.session_id, a.id)

            def publish():
                if check:
                    check()
                if self.upload_path(lease).stat().st_size != size:
                    raise ValueError("The attachment upload is incomplete")
                d.mkdir(parents=True)
                self.upload_path(lease).replace(d / ("source" + Path(filename).suffix.lower()))
                self._write(a)

            try:
                self.resources.commit(lease, size, publish)
            except BaseException:
                shutil.rmtree(d, ignore_errors=True)
                self.resources.abort(lease)
                raise
        return a

    def get(self, session_id: str, attachment_id: str) -> Attachment | None:
        with _LOCK:
            try:
                return Attachment.model_validate_json((self._dir(session_id, attachment_id) / "metadata.json").read_text())
            except FileNotFoundError:
                return None

    def list(self, session_id: str, *, bound_only: bool = False) -> list[Attachment]:
        if not _ID.fullmatch(session_id):
            return []  # Older API clients may have created arbitrary session IDs.
        with _LOCK:
            out = []
            for p in sorted(self._dir(session_id).glob("*/metadata.json")):
                a = self.get(session_id, p.parent.name)
                if a and (a.bound or not bound_only):
                    out.append(a)
            return sorted(out, key=lambda a: a.created_at)

    def update(self, session_id: str, attachment_id: str, **changes) -> Attachment | None:
        with _LOCK:
            a = self.get(session_id, attachment_id)
            if a is None:
                return None
            a = a.model_copy(update=changes)
            self._write(a)
            return a

    def source_path(self, session_id: str, attachment_id: str) -> Path:
        a = self.get(session_id, attachment_id)
        if a is None:
            raise ValueError("Attachment not found")
        return self._dir(session_id, attachment_id) / ("source" + Path(a.filename).suffix.lower())

    def text(self, session_id: str, attachment_id: str) -> str:
        return (self._dir(session_id, attachment_id) / "parsed.md").read_text(encoding="utf-8")

    def parsed(self, session_id: str, attachment_id: str) -> dict:
        return json.loads((self._dir(session_id, attachment_id) / "parsed.json").read_text())

    def image_url(self, session_id: str, attachment_id: str, index: int) -> str:
        if index < 0:
            raise ValueError("Image index is out of range")
        path = self._dir(session_id, attachment_id) / f"image-{index}.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        images = self.parsed(session_id, attachment_id)["images"]
        if index >= len(images):
            raise ValueError("Image index is out of range")
        ref = images[index]
        if ref.get("render_source") not in {"source", "pages"}:
            raise FileNotFoundError(path)
        from src.chat_attachments.rendering import render_page
        source = self.source_path(session_id, attachment_id) if ref["render_source"] == "source" else path.parent / "pages.pdf"
        rendered = render_page(source, ref["page_number"])
        with _LOCK:
            if self.get(session_id, attachment_id) is None:
                raise FileNotFoundError(path)
            if not path.exists():
                path.write_text(rendered, encoding="utf-8")
            return path.read_text(encoding="utf-8")

    def finish(self, session_id: str, attachment_id: str, *, content: str, position_map: list,
               images: list[dict], parser: str, warnings: list[str] | None = None,
               native: bool = False, text_prepared: bool = False, description_model: str = "") -> None:
        with _LOCK:
            if self.get(session_id, attachment_id) is None:
                return  # explicit deletion while parsing must never resurrect files
            d = self._dir(session_id, attachment_id)
            (d / "parsed.md").write_text(content, encoding="utf-8")
            if native:
                (d / "native.md").write_text(content, encoding="utf-8")
            refs = []
            for i, img in enumerate(images):
                ref = {k: v for k, v in img.items() if k != "url"}
                if img.get("url"):
                    (d / f"image-{i}.txt").write_text(img["url"], encoding="utf-8")
                refs.append(ref)
            (d / "parsed.json").write_text(json.dumps({"position_map": position_map, "images": refs}, ensure_ascii=False), encoding="utf-8")
            self.update(session_id, attachment_id, status="ready", error="", parser=parser,
                        warnings=warnings or [], characters=len(content), lines=len(content.splitlines()), image_count=len(images),
                        text_prepared=text_prepared, description_model=description_model)

    def native_text(self, session_id: str, attachment_id: str) -> str:
        path = self._dir(session_id, attachment_id) / "native.md"
        return path.read_text(encoding="utf-8") if path.exists() else self.text(session_id, attachment_id)

    def bind(self, session_id: str, ids: list[str]) -> list[Attachment]:
        with _LOCK:
            if len(ids) > MAX_ATTACHMENTS or len(set(ids)) != len(ids):
                raise ValueError("Choose up to 10 distinct attachments")
            rows = [self.get(session_id, aid) for aid in ids]
            if any(a is None or a.status != "ready" for a in rows):
                raise ValueError("All attachments must belong to this session and be ready")
            return [self.update(session_id, a.id, bound=True) for a in rows]

    def remove(self, session_id: str, attachment_id: str) -> None:
        with _LOCK:
            a = self.get(session_id, attachment_id)
            if a is None:
                raise ValueError("Attachment not found")
            if a.bound:
                raise ValueError("Sent attachments are retained with the conversation")
            self.resources.delete(session_id, attachment_id,
                                  lambda: shutil.rmtree(self._dir(session_id, attachment_id)))

    def purge(self, session_id: str) -> None:
        if not _ID.fullmatch(session_id):
            return
        with _LOCK:
            self.resources.delete(session_id, None,
                                  lambda: shutil.rmtree(self._dir(session_id), ignore_errors=True))


def for_sessions(session_store) -> AttachmentStore:
    from src.config import get_data_dir
    db_path = getattr(session_store, "_db_path", None)
    base = db_path.parent if isinstance(db_path, Path) else get_data_dir()
    def owner(sid):
        session = session_store.get_session(sid)
        return getattr(session, "created_by", None)

    return AttachmentStore(base / "chat_attachments", owner)
