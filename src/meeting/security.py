"""Temporary signed tokens for secure audio file access."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

# Server secret generated at startup — tokens are invalidated on restart
_SECRET = os.environ.get("MEETING_TOKEN_SECRET") or secrets.token_hex(32)


def verify_audio_token(meeting_id: str, token: str) -> bool:
    """Verify an audio access token.

    Returns True if the token is valid and not expired.
    """
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return False
        expires_str, sig = parts
        expires = int(expires_str)

        # Check expiry
        if time.time() > expires:
            return False

        # Verify signature
        payload = f"{meeting_id}.{expires}"
        expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except (ValueError, IndexError):
        return False
