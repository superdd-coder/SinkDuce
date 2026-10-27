"""Limits and API contracts for conversation report artifacts."""
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

MAX_MARKDOWN = 120_000
RENDERER_VERSION = "4"
MAX_CITATIONS = 256
MAX_ASSETS = 20
MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ASSETS = 25 * 1024 * 1024
FORMAT_TYPES = {
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def validate_content(title, markdown):
    if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > MAX_MARKDOWN:
        raise ValueError("Report content must contain 1–120000 characters")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Report title must contain 1–200 characters")


def _caption_issues(markdown, citations):
    by_id = {e["id"]: e for e in citations}
    fence = ""
    for line in markdown.splitlines():
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if marker:
            fence = "" if fence == marker[1][0] else marker[1][0]
        if fence or not re.match(r"^\s*(?:\*\*)?(?:来源|数据来源|资料来源|Sources?|Data source)\s*[：:]", line, re.IGNORECASE):
            continue
        for segment in re.split(r"[；;]", line):
            pages = re.findall(r"第\s*(\d+)\s*页|\bpage\s+(\d+)\b", segment, re.IGNORECASE)
            numbers = {int(a or b) for a, b in pages}
            if len(numbers) != 1:
                continue
            expected = next(iter(numbers))
            for eid in re.findall(r"\[cite:([^\]]+)\]", segment):
                location = by_id.get(eid, {}).get("location", {})
                actual = location.get("page_number", location.get("slide_number"))
                if actual is not None and actual != expected:
                    yield segment.strip(), eid, expected, actual


def validate_caption_pages(markdown, citations, *, previous=None):
    """Validate new claims; an untouched legacy caption never blocks an unrelated edit."""
    inherited = Counter(_caption_issues(previous['markdown'], previous['citations'])) if previous else Counter()
    for issue in _caption_issues(markdown, citations):
        if inherited[issue]:
            inherited[issue] -= 1
            continue
        _, eid, expected, actual = issue
        raise ValueError(f"Caption cites page {expected}, but {eid} belongs to page {actual}. Read the requested page and use its evidence ID.")


class ExportRequest(BaseModel):
    format: Literal["html", "docx", "pdf"]


class RestoreRequest(BaseModel):
    expected_revision: int = Field(ge=1, strict=True)
    operation_id: str = Field(min_length=1, max_length=180)


class ReportReference(BaseModel):
    id: str
    session_id: str
    title: str
    revision: int
    updated_at: str


class CommentAnchor(BaseModel):
    kind: Literal['text', 'block'] = 'text'
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=8000)
    prefix: str = Field(default="", max_length=100)
    suffix: str = Field(default="", max_length=100)
    block_id: str | None = Field(default=None, pattern=r'^b_[a-f0-9]{32}$')
    block_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    end_block_id: str | None = Field(default=None, pattern=r'^b_[a-f0-9]{32}$')
    end_block_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    # Zero-based code-point offsets in the selected line range's visible text,
    # after excluding whitespace and citation controls. Neither is a JS offset.
    selection_start: int | None = Field(default=None, ge=0, strict=True)
    selection_end: int | None = Field(default=None, ge=1, strict=True)


class CommentText(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class CommentCreate(CommentText):
    anchor: CommentAnchor


class ReviewRequest(BaseModel):
    report_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    comment_ids: list[str] = Field(min_length=1, max_length=100)


class DecisionRequest(RestoreRequest):
    decision: Literal['accepted', 'rejected']
    scope: Literal['location', 'instruction'] = 'location'


class FollowUpRequest(RestoreRequest, CommentText):
    pass
