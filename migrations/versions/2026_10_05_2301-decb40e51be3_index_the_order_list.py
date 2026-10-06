"""Index the order list's filters: status, customer and creator (ADR-0014).

Revision ID: decb40e51be3
Revises: 837c6f4483f3
Create Date: 2026-10-05 23:01:01.029413+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "decb40e51be3"
down_revision: str | Sequence[str] | None = "837c6f4483f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_orders_tenant_id_created_by_id_id"),
        "orders",
        ["tenant_id", "created_by_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_tenant_id_customer_id_id"),
        "orders",
        ["tenant_id", "customer_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_tenant_id_status_id"), "orders", ["tenant_id", "status", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_tenant_id_status_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_tenant_id_customer_id_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_tenant_id_created_by_id_id"), table_name="orders")
