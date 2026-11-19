"""Enforce upload limits while the ASGI application consumes the body."""
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(MultiPartException):
    """Also makes Starlette close any partially written multipart files."""


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.max_bytes
        if scope["path"].startswith("/api/mcp/"):
            from src.mcp.staging import MAX_TOTAL_BYTES
            limit = min(limit, MAX_TOTAL_BYTES) if limit else MAX_TOTAL_BYTES
        if not limit:
            return await self.app(scope, receive, send)

        rejection = JSONResponse({"error": "Payload too large"}, status_code=413)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                length = int(declared)
                if length < 0:
                    raise ValueError
            except ValueError:
                return await JSONResponse({"error": "Invalid content length"}, 400)(scope, receive, send)
            if length > limit:
                return await rejection(scope, receive, send)

        received = 0
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge("Payload too large")
            return message

        async def bounded_send(message: Message):
            nonlocal response_started
            if received > limit and not response_started:
                return  # The parser may have translated the size error into HTTP 400.
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except _BodyTooLarge:
            if response_started:
                raise
        if received > limit and not response_started:
            await rejection(scope, receive, send)
