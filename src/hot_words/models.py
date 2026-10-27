from __future__ import annotations

from pydantic import BaseModel, Field


class HotWordItem(BaseModel):
    text: str = ""
    weight: int = Field(default=4, ge=1, le=10)
    lang: str = ""


class HotWordsLibrary(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    words: list[HotWordItem] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
