"""Add indexes for the conversation list query."""

from alembic import op

revision = "0007_add_conversation_list_index"
down_revision = "0006_add_user_withdrawal"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_conversations_list",
        "conversations",
        ["user_id", "updated_at", "conversation_id"],
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_index("ix_conversations_list", table_name="conversations")
