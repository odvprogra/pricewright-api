"""Add service accounts and API keys.

Revision ID: dbd4548ac0e2
Revises: 30d7ec8c4871
Create Date: 2026-10-03 17:38:40.983013+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dbd4548ac0e2"
down_revision: str | Sequence[str] | None = "30d7ec8c4871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_service_accounts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_accounts")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_service_accounts_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_service_accounts_tenant_id_name")),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_account_id", sa.Uuid(), nullable=False),
        sa.Column("key_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("hint", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "service_account_id"],
            ["service_accounts.tenant_id", "service_accounts.id"],
            name=op.f("fk_api_keys_tenant_id_service_account_id_service_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_api_keys_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_digest", name=op.f("uq_api_keys_key_digest")),
    )
    op.create_index(
        op.f("ix_api_keys_tenant_id_service_account_id"),
        "api_keys",
        ["tenant_id", "service_account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_tenant_id_service_account_id"), table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("service_accounts")
