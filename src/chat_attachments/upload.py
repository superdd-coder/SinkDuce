"""One-file multipart streaming after the caller reserves disk and work capacity."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from src.chat_attachments.store import AttachmentStore

CHUNK_BYTES = 64 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
UPLOAD_TIMEOUT = 120


def check_envelope(request: Request, max_bytes: int) -> None:
    media, _ = parse_options_header(request.headers.get("content-type", ""))
    if media != b"multipart/form-data":
        raise HTTPException(400, "Expected one multipart attachment")
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length") from None
        if length < 0:
            raise HTTPException(400, "Invalid Content-Length")
        if length > max_bytes + MAX_ENVELOPE_BYTES:
            raise HTTPException(413, "File exceeds the upload size limit")


async def stream_source(request: Request, path: Path, *, max_bytes: int, extensions: set[str]) -> tuple[str, int]:
    """Write source chunks straight to its reserved file; never spool a second copy."""
    _, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if not boundary or len(boundary) > 200:
        raise HTTPException(400, "Invalid multipart boundary")
    filename, size, parts, complete = "", 0, 0, False
    header_name, header_value = bytearray(), bytearray()
    headers = {}
    header_bytes = 0

    def part_begin():
        nonlocal parts
        parts += 1
        if parts > 1:
            raise HTTPException(400, "Upload exactly one file at a time")

    def header_field(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > MAX_ENVELOPE_BYTES:
            raise HTTPException(400, "Attachment headers are too large")
        header_name.extend(data[start:end])

    def header_content(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > MAX_ENVELOPE_BYTES:
            raise HTTPException(400, "Attachment headers are too large")
        header_value.extend(data[start:end])

    def header_end():
        name = bytes(header_name).lower()
        if name in headers:
            raise HTTPException(400, "Duplicate attachment header")
        headers[name] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        nonlocal filename
        disposition, fields = parse_options_header(headers.get(b"content-disposition", b""))
        if disposition != b"form-data" or fields.get(b"name") != b"file" or b"filename" not in fields:
            raise HTTPException(400, "Expected one file field named file")
        try:
            name = fields[b"filename"].decode("utf-8")
        except UnicodeDecodeError:
            name = fields[b"filename"].decode("latin-1")
        filename = AttachmentStore.validate_filename(name)
        if Path(filename).suffix.lower() not in extensions:
            raise HTTPException(400, "This file type is not supported by the configured parsers")

    def part_data(data, start, end):
        nonlocal size
        size += end - start
        if size > max_bytes:
            raise HTTPException(413, "File exceeds the upload size limit")
        output.write(memoryview(data)[start:end])

    def ended():
        nonlocal complete
        complete = True

    parser = MultipartParser(boundary, {
        "on_part_begin": part_begin, "on_header_field": header_field,
        "on_header_value": header_content, "on_header_end": header_end,
        "on_headers_finished": headers_finished, "on_part_data": part_data,
        "on_end": ended,
    })
    received = 0
    try:
        with path.open("xb") as output:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > max_bytes + MAX_ENVELOPE_BYTES:
                        raise HTTPException(413, "Attachment request exceeds the upload size limit")
                    for start in range(0, len(chunk), CHUNK_BYTES):
                        # Shield this bounded disk write before closing/unlinking
                        # its destination on timeout or client cancellation.
                        task = asyncio.create_task(run_in_threadpool(parser.write, chunk[start:start + CHUNK_BYTES]))
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            await task
                            raise
                parser.finalize()
            if not complete or parts != 1 or not filename or not size:
                raise HTTPException(400, "The attachment is empty or its upload is incomplete")
    except TimeoutError:
        raise HTTPException(408, "Attachment upload timed out") from None
    except ClientDisconnect:
        raise HTTPException(400, "Attachment upload was interrupted") from None
    except (MultipartParseError, ValueError) as exc:
        raise HTTPException(400, "Invalid attachment upload") from exc
    return filename, size
