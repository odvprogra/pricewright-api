"""Version tenants for optimistic concurrency.

Revision ID: 139777c1f65f
Revises: c4fe82e6694d
Create Date: 2026-10-03 16:49:03.261648+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "139777c1f65f"
down_revision: str | Sequence[str] | None = "c4fe82e6694d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants", sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )
    # Autogenerate does not detect check constraints.
    op.create_check_constraint(op.f("ck_tenants_version_positive"), "tenants", "version >= 1")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tenants_version_positive"), "tenants", type_="check")
    op.drop_column("tenants", "version")
