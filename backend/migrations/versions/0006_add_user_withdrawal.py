"""Add soft deletion and shared withdrawal attempt limits."""

import sqlalchemy as sa
from alembic import op

revision = "0006_add_user_withdrawal"
down_revision = "0005_add_user_profile_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "withdrawal_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("withdrawal_rate_limits")
    op.drop_column("users", "deleted_at")
