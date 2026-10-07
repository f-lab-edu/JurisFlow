"""Create the access token blacklist."""

import sqlalchemy as sa
from alembic import op

revision = "0002_create_token_blacklist"
down_revision = "0001_create_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_blacklist",
        sa.Column("token", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_token_blacklist_expires_at", "token_blacklist", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_token_blacklist_expires_at", table_name="token_blacklist")
    op.drop_table("token_blacklist")
