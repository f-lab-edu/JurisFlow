"""Store profile metadata without inventing consent for existing users."""

import sqlalchemy as sa
from alembic import op

revision = "0005_add_user_profile_fields"
down_revision = "0004_create_conversation_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("terms_agreed", sa.Boolean(), nullable=True))
    op.add_column("users", sa.Column("privacy_agreed", sa.Boolean(), nullable=True))
    op.add_column(
        "users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("users", "updated_at")
    op.drop_column("users", "privacy_agreed")
    op.drop_column("users", "terms_agreed")
