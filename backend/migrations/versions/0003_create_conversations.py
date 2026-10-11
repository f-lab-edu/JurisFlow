"""Persist conversations, messages and idempotent generation requests."""

import sqlalchemy as sa
from alembic import op

revision = "0003_create_conversations"
down_revision = "0002_create_token_blacklist"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_table(
        "messages",
        sa.Column("message_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("parent_message_id", sa.Uuid(), sa.ForeignKey("messages.message_id")),
        sa.Column("idempotency_key", sa.Uuid()),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("finish_reason", sa.String(40)),
        sa.Column("usage", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_messages_idempotency"
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index(
        "uq_messages_active",
        "messages",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'streaming'"),
        sqlite_where=sa.text("status = 'streaming'"),
    )


def downgrade():
    op.drop_table("messages")
    op.drop_table("conversations")
