"""Read only managed source images, scoped to the cited file/version or session."""
from __future__ import annotations

import base64
import re
from pathlib import Path

from fastapi import HTTPException

from src.identity import get_actor
from src.reports.models import MAX_ASSET_BYTES

_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class SourceImages:
    def __init__(self, session_id: str, attachments_root: Path):
        self.session_id = session_id
        self.attachments_root = attachments_root

    def __call__(self, evidence: dict, fields: dict) -> bytes | None:
        try:
            meta = evidence.get("source") or {}
            if meta.get("attachment_id"):
                return self._attachment(evidence, fields)
            from src.collections import store
            from src.file_mgmt import storage_paths
            raw = str(meta.get("collection") or meta.get("collection_name") or "")
            col = store.get_collection_meta(raw) or store.find_collection_by_name(raw)
            collection = str(col["id"]) if col else raw
            file_id = str(meta.get("file_id") or "")
            if not file_id and str(meta.get("source", "")).startswith("__file__:"):
                file_id = str(meta["source"])[9:]
            image_id = str(fields.get("image_id") or "")
            if fields.get("file_id") and fields["file_id"] != file_id:
                return None
            if not all(_ID.fullmatch(value) for value in (collection, file_id, image_id)):
                return None
            actor = get_actor()
            if actor is not None:
                from src.identity.visibility import assert_can_read
                assert_can_read(actor, collection)
            root = storage_paths.managed_file_dir(collection, file_id)
            version = str(meta.get("version_id") or "")
            if version:
                if not _ID.fullmatch(version):
                    return None
                root = root / version  # Never search another version for a missing asset.
            if not version:
                path = storage_paths.find_image_file(collection, file_id, image_id)
                if path and path.resolve().is_relative_to(root.resolve()) and path.stat().st_size <= MAX_ASSET_BYTES:
                    return path.read_bytes()
            for ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"):
                path = root / "images" / f"{image_id}.{ext}"
                if path.is_file() and path.resolve().is_relative_to(root.resolve()) and path.stat().st_size <= MAX_ASSET_BYTES:
                    return path.read_bytes()
        except (OSError, ValueError, KeyError, HTTPException):
            pass
        return None

    def _attachment(self, evidence, fields):
        from src.chat_attachments.store import AttachmentStore
        store = AttachmentStore(self.attachments_root)
        aid = str(evidence["source"]["attachment_id"])
        if store.get(self.session_id, aid) is None:
            return None
        images = store.parsed(self.session_id, aid)["images"]
        image_id = fields.get("image_id")
        index = next((i for i, ref in enumerate(images) if ref.get("id") == image_id), None) if image_id else (evidence.get("location") or {}).get("image_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(images):
            return None
        data = store.image_url(self.session_id, aid, index)
        if len(data) > MAX_ASSET_BYTES * 4 // 3 + 100 or not re.match(r"^data:image/(?:png|jpeg|webp|gif|bmp);base64,", data):
            return None
        return base64.b64decode(data.split(",", 1)[1], validate=True)
