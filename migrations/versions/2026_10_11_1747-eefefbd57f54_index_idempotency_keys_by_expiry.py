"""Index idempotency keys by their expiry, for the worker's purge (ADR-0022).

Revision ID: eefefbd57f54
Revises: ffdf07a2cef6
Create Date: 2026-10-11 17:47:53.441658+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "eefefbd57f54"
down_revision: str | Sequence[str] | None = "ffdf07a2cef6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_idempotency_keys_expires_at"), "idempotency_keys", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_idempotency_keys_expires_at"), table_name="idempotency_keys")
