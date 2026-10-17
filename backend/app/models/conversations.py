from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.users import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_list", "user_id", "updated_at", "conversation_id"),
    )
    conversation_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_messages_idempotency"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index(
            "uq_messages_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'streaming'"),
            sqlite_where=text("status = 'streaming'"),
        ),
    )
    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.conversation_id"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    parent_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.message_id")
    )
    idempotency_key: Mapped[UUID | None] = mapped_column()
    request_hash: Mapped[str | None] = mapped_column(String(64))
    finish_reason: Mapped[str | None] = mapped_column(String(40))
    usage: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# 메시지 저장과 체크포인트 실행을 연결해 중단된 요청을 복구한다.
class ConversationRun(Base):
    __tablename__ = "conversation_runs"
    __table_args__ = (
        Index(
            "uq_conversation_runs_pending",
            "conversation_id",
            unique=True,
            postgresql_where=text("status != 'completed'"),
            sqlite_where=text("status != 'completed'"),
        ),
    )

    assistant_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.message_id"), primary_key=True
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.conversation_id"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
