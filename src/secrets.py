"""Mask secrets in API responses. Disk still stores the real value."""

from __future__ import annotations

from typing import Any

# ASCII-only: must be safe in HTTP Authorization headers if echoed back.
_MASK = "****"
_LEGACY_MASK = "••••"


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return _MASK
    return _MASK + value[-4:]


def skip_secret_write(key: str, value: Any) -> bool:
    """True when an incoming field must not overwrite the stored secret."""
    if not _is_secret_key(key):
        return False
    if value is None or value == "":
        return True
    return isinstance(value, str) and is_masked_secret(value)


def is_masked_secret(value: str | None) -> bool:
    if not value:
        return False
    for prefix in (_MASK, _LEGACY_MASK):
        if value == prefix or (value.startswith(prefix) and len(value) > len(prefix)):
            return True
    return False


def effective_secret(incoming: str | None, stored: str | None) -> str:
    """Prefer a newly typed secret; keep stored when the client echoed a mask."""
    if incoming is None or incoming == "" or is_masked_secret(incoming):
        return stored or ""
    return incoming


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k == "api_key" or k.endswith("_api_key") or k in {"token", "api_token"}


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in data.items():
        if isinstance(val, dict):
            out[key] = redact_mapping(val)
        elif isinstance(val, list):
            out[key] = [
                redact_mapping(item) if isinstance(item, dict) else item for item in val
            ]
        elif _is_secret_key(key) and isinstance(val, str):
            out[key] = mask_secret(val)
        else:
            out[key] = val
    return out


def restore_secrets(incoming: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """Replace masked/empty secret fields in incoming with stored values."""
    out = dict(incoming)
    for key, val in incoming.items():
        if isinstance(val, dict) and isinstance(stored.get(key), dict):
            out[key] = restore_secrets(val, stored[key])
        elif isinstance(val, list) and isinstance(stored.get(key), list):
            stored_list = stored[key]
            merged = []
            for i, item in enumerate(val):
                if isinstance(item, dict) and i < len(stored_list) and isinstance(stored_list[i], dict):
                    merged.append(restore_secrets(item, stored_list[i]))
                else:
                    merged.append(item)
            out[key] = merged
        elif _is_secret_key(key) and isinstance(val, str) and isinstance(stored.get(key), str):
            if is_masked_secret(val) or val == "":
                out[key] = stored[key]
    return out
