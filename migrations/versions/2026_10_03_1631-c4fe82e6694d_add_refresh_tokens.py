"""Add refresh tokens.

Revision ID: c4fe82e6694d
Revises: 87506955f1ab
Create Date: 2026-10-03 16:31:31.201688+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4fe82e6694d"
down_revision: str | Sequence[str] | None = "87506955f1ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at <= family_expires_at", name=op.f("ck_refresh_tokens_expires_within_family")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_refresh_tokens_tenant_id_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_refresh_tokens_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_digest", name=op.f("uq_refresh_tokens_token_digest")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_tenant_id_family_id"),
        "refresh_tokens",
        ["tenant_id", "family_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_tenant_id_family_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
