"""Add idempotency keys: what a request with an Idempotency-Key created (ADR-0022).

Revision ID: 9e328a07b6e7
Revises: d4e68a65e08e
Create Date: 2026-10-05 21:09:00.892198+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e328a07b6e7"
down_revision: str | Sequence[str] | None = "d4e68a65e08e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service_account')",
            name=op.f("ck_idempotency_keys_actor_type_is_known"),
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_idempotency_keys_fingerprint_is_sha256")
        ),
        sa.CheckConstraint("idempotency_key <> ''", name=op.f("ck_idempotency_keys_key_not_empty")),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_idempotency_keys_expires_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_idempotency_keys_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "actor_type",
            "actor_id",
            "idempotency_key",
            name=op.f("pk_idempotency_keys"),
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
