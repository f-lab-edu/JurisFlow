from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from backend.app.schemas.auth import ResponseMeta, ResponseModel


class MessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    parent_message_id: UUID | None = Field(default=None, alias="parentMessageId")

    @field_validator("content")
    @classmethod
    def trim_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("메시지를 입력해 주세요.")
        return value


class ConversationRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError("제목을 입력해 주세요.")
        return value.strip() if value else value


class UpdateTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value):
        return value.strip() if isinstance(value, str) else value


def _utc_iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class LastMessage(ResponseModel):
    role: str
    content_preview: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value):
        return _utc_iso(value)


class ConversationItem(ResponseModel):
    conversation_id: UUID
    title: str
    last_message: LastMessage | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_times(self, value):
        return _utc_iso(value)


class PageInfo(ResponseModel):
    next_cursor: str | None
    has_next: bool
    limit: int


class ConversationListData(ResponseModel):
    items: list[ConversationItem]
    page: PageInfo


class ConversationListResponse(ResponseModel):
    success: Literal[True] = True
    data: ConversationListData
    meta: ResponseMeta


class ConversationTitleData(ResponseModel):
    conversation_id: UUID
    title: str
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_updated_at(self, value):
        return _utc_iso(value)


class ConversationTitleResponse(ResponseModel):
    success: Literal[True] = True
    data: ConversationTitleData
    meta: ResponseMeta


class StopGenerationData(ResponseModel):
    message_id: UUID
    status: Literal["stopped"]
    stopped_at: datetime

    @field_serializer("stopped_at")
    def serialize_stopped_at(self, value):
        return _utc_iso(value)


class StopGenerationResponse(ResponseModel):
    success: Literal[True] = True
    data: StopGenerationData
    meta: ResponseMeta
