"""Add the order number prefix to tenants.

Existing tenants get ORD, the prefix Dynamics 365 Sales gives orders, or SO if their quotes already
use ORD: a quote and an order must never share a number. New tenants always state it, so the column
keeps no server default.

Revision ID: 11744420ea5c
Revises: 9e328a07b6e7
Create Date: 2026-10-05 21:47:45.533106+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "11744420ea5c"
down_revision: str | Sequence[str] | None = "9e328a07b6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("order_prefix", sa.String(length=5), server_default="ORD", nullable=False),
    )
    op.execute("UPDATE tenants SET order_prefix = 'SO' WHERE quote_prefix = 'ORD'")
    op.alter_column("tenants", "order_prefix", server_default=None)
    # Autogenerate does not detect check constraints on an existing table.
    op.create_check_constraint(
        op.f("ck_tenants_order_prefix_is_valid"),
        "tenants",
        "order_prefix ~ '^[A-Z][A-Z0-9]{1,4}$'",
    )
    op.create_check_constraint(
        op.f("ck_tenants_prefixes_differ"), "tenants", "order_prefix <> quote_prefix"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tenants_prefixes_differ"), "tenants", type_="check")
    op.drop_constraint(op.f("ck_tenants_order_prefix_is_valid"), "tenants", type_="check")
    op.drop_column("tenants", "order_prefix")
