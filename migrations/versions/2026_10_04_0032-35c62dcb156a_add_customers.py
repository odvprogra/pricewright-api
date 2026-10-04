"""Add customers, keyed by account number per tenant.

Revision ID: 35c62dcb156a
Revises: a4c5d9dfc893
Create Date: 2026-10-04 00:32:18.421379+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "35c62dcb156a"
down_revision: str | Sequence[str] | None = "a4c5d9dfc893"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_number", sa.String(length=20, collation="unicode"), nullable=False),
        sa.Column("name", sa.String(length=200, collation="unicode"), nullable=False),
        sa.Column("tax_id", sa.String(length=30), nullable=True),
        sa.Column("tier", sa.String(length=10), nullable=False),
        sa.Column("payment_terms_days", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.CheckConstraint("tax_id ~ '^[A-Z0-9]+$'", name=op.f("ck_customers_tax_id_is_compact")),
        sa.CheckConstraint(
            "tier IN ('standard', 'silver', 'gold')", name=op.f("ck_customers_tier_is_known")
        ),
        sa.CheckConstraint(
            "payment_terms_days BETWEEN 0 AND 365",
            name=op.f("ck_customers_payment_terms_days_in_range"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_customers_version_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_customers_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_customers_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_customers_tenant_id_account_number_id"),
        "customers",
        ["tenant_id", "account_number", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customers_tenant_id_name_id"),
        "customers",
        ["tenant_id", "name", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customers_tenant_id_tax_id"), "customers", ["tenant_id", "tax_id"], unique=False
    )
    op.create_index(
        "uq_customers_tenant_id_lower_account_number",
        "customers",
        ["tenant_id", sa.literal_column("lower(account_number)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_customers_tenant_id_lower_account_number", table_name="customers")
    op.drop_index(op.f("ix_customers_tenant_id_tax_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_tenant_id_name_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_tenant_id_account_number_id"), table_name="customers")
    op.drop_table("customers")
