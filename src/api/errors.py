from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    """User-visible API error with a stable code for frontend i18n."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "params": params or {},
                "message": message,
            },
        )
