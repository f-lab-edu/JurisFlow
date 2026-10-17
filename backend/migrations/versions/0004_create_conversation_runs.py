"""Track checkpoint-backed chat requests without changing the public API."""

import sqlalchemy as sa
from alembic import op

revision = "0004_create_conversation_runs"
down_revision = "0003_create_conversations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_runs",
        sa.Column(
            "assistant_message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.message_id"),
            primary_key=True,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_conversation_runs_conversation_id", "conversation_runs", ["conversation_id"]
    )
    op.create_index(
        "uq_conversation_runs_pending",
        "conversation_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status != 'completed'"),
        sqlite_where=sa.text("status != 'completed'"),
    )


def downgrade():
    # Checkpoint tables belong to LangGraph and are intentionally not dropped here.
    op.drop_table("conversation_runs")
