"""Version users for optimistic concurrency.

Revision ID: 30d7ec8c4871
Revises: 139777c1f65f
Create Date: 2026-10-03 17:08:52.671673+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "30d7ec8c4871"
down_revision: str | Sequence[str] | None = "139777c1f65f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )
    # Autogenerate does not detect check constraints.
    op.create_check_constraint(op.f("ck_users_version_positive"), "users", "version >= 1")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_version_positive"), "users", type_="check")
    op.drop_column("users", "version")
