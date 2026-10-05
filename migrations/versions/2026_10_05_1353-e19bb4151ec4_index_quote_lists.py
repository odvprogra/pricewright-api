"""Index the quote list's sorts and filters (ADR-0014).

Revision ID: e19bb4151ec4
Revises: 79a54718146a
Create Date: 2026-10-05 13:53:28.611288+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e19bb4151ec4"
down_revision: str | Sequence[str] | None = "79a54718146a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_quotes_tenant_id_created_by_id_id"),
        "quotes",
        ["tenant_id", "created_by_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_quotes_tenant_id_customer_id_id"),
        "quotes",
        ["tenant_id", "customer_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_quotes_tenant_id_status_id"), "quotes", ["tenant_id", "status", "id"], unique=False
    )
    op.create_index(
        op.f("ix_quotes_tenant_id_valid_until_id"),
        "quotes",
        ["tenant_id", "valid_until", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_quotes_tenant_id_valid_until_id"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_tenant_id_status_id"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_tenant_id_customer_id_id"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_tenant_id_created_by_id_id"), table_name="quotes")
