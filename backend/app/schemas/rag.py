from typing import Literal

from pydantic import BaseModel, Field


class IngestResult(BaseModel):
    filename: str
    status: Literal["stored", "skipped", "failed"]
    document_id: str | None = None
    pages: int = 0
    chunks_created: int = 0
    chunks_stored: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=50)
    document_id: str | None = None


class SearchHit(BaseModel):
    chunk_id: str
    text: str
    metadata: dict
    distance: float
