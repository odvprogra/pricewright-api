"""Index approval requests by status, oldest first: the approval inbox (ADR-0020).

Revision ID: d4e68a65e08e
Revises: e19bb4151ec4
Create Date: 2026-10-05 15:03:10.911112+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e68a65e08e"
down_revision: str | Sequence[str] | None = "e19bb4151ec4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_approval_requests_tenant_id_status_id"),
        "approval_requests",
        ["tenant_id", "status", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_requests_tenant_id_status_id"), table_name="approval_requests")
