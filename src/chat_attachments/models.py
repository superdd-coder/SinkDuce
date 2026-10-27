from typing import Literal

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    id: str
    session_id: str
    filename: str
    size: int
    status: Literal["processing", "ready", "failed"] = "processing"
    parser: str = ""
    error: str = ""
    warnings: list[str] = Field(default_factory=list)
    characters: int = 0
    lines: int = 0
    image_count: int = 0
    text_prepared: bool = False
    description_model: str = ""
    bound: bool = False
    created_at: str


MAX_ATTACHMENTS = 10
MAX_FILE_BYTES = 50 * 1024 * 1024
