"""Add tenants.

Revision ID: 280ae32565b1
Revises:
Create Date: 2026-10-03 15:16:06.538979+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "280ae32565b1"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("tax_rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("approval_threshold", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_tenants_currency_is_iso_4217")),
        sa.CheckConstraint(
            "approval_threshold >= 0 AND approval_threshold <= 1",
            name=op.f("ck_tenants_approval_threshold_in_range"),
        ),
        sa.CheckConstraint(
            "tax_rate >= 0 AND tax_rate < 1", name=op.f("ck_tenants_tax_rate_in_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )


def downgrade() -> None:
    op.drop_table("tenants")
