from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
