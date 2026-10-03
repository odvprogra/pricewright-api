"""Add users.

Revision ID: d98f67df2938
Revises: 280ae32565b1
Create Date: 2026-10-03 15:24:05.873555+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d98f67df2938"
down_revision: str | Sequence[str] | None = "280ae32565b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint(
            "role IN ('sales_rep', 'sales_manager', 'admin')", name=op.f("ck_users_role_is_known")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_is_lowercase")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_users_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_users_tenant_id_id")),
    )


def downgrade() -> None:
    op.drop_table("users")
