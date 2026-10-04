"""Add product categories, named uniquely per tenant ignoring case.

Revision ID: 99b241d116f1
Revises: 077ada8521b0
Create Date: 2026-10-04 00:05:10.858156+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "99b241d116f1"
down_revision: str | Sequence[str] | None = "077ada8521b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_categories",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100, collation="unicode"), nullable=False),
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
        sa.CheckConstraint("version >= 1", name=op.f("ck_product_categories_version_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_product_categories_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_categories")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_product_categories_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_product_categories_tenant_id_name_id"),
        "product_categories",
        ["tenant_id", "name", "id"],
        unique=False,
    )
    op.create_index(
        "uq_product_categories_tenant_id_lower_name",
        "product_categories",
        ["tenant_id", sa.literal_column("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_product_categories_tenant_id_lower_name", table_name="product_categories")
    op.drop_index(op.f("ix_product_categories_tenant_id_name_id"), table_name="product_categories")
    op.drop_table("product_categories")
