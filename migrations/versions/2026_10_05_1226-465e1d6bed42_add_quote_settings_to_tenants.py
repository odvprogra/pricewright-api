"""Add the quote number prefix and the default quote validity to tenants.

Existing tenants get the defaults (QUO, 30 days) and admins change them; new tenants always state
them, so the columns keep no server default.

Revision ID: 465e1d6bed42
Revises: 32c10a98fba8
Create Date: 2026-10-05 12:26:38.543216+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "465e1d6bed42"
down_revision: str | Sequence[str] | None = "32c10a98fba8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("quote_prefix", sa.String(length=5), server_default="QUO", nullable=False),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "quote_validity_days", sa.SmallInteger(), server_default=sa.text("30"), nullable=False
        ),
    )
    op.alter_column("tenants", "quote_prefix", server_default=None)
    op.alter_column("tenants", "quote_validity_days", server_default=None)
    # Autogenerate does not detect check constraints on an existing table.
    op.create_check_constraint(
        op.f("ck_tenants_quote_prefix_is_valid"),
        "tenants",
        "quote_prefix ~ '^[A-Z][A-Z0-9]{1,4}$'",
    )
    op.create_check_constraint(
        op.f("ck_tenants_quote_validity_days_in_range"),
        "tenants",
        "quote_validity_days BETWEEN 1 AND 365",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tenants_quote_validity_days_in_range"), "tenants", type_="check")
    op.drop_constraint(op.f("ck_tenants_quote_prefix_is_valid"), "tenants", type_="check")
    op.drop_column("tenants", "quote_validity_days")
    op.drop_column("tenants", "quote_prefix")
