"""Add products, priced in their tenant's currency.

Revision ID: a4c5d9dfc893
Revises: 99b241d116f1
Create Date: 2026-10-04 00:14:35.091626+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c5d9dfc893"
down_revision: str | Sequence[str] | None = "99b241d116f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # First: the target of the products' composite key to the tenant's currency.
    op.create_unique_constraint(op.f("uq_tenants_id_currency"), "tenants", ["id", "currency"])
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=40, collation="unicode"), nullable=False),
        sa.Column("name", sa.String(length=200, collation="unicode"), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("unit", sa.String(length=3), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("list_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
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
        sa.CheckConstraint(
            "unit IN ('EA', 'PR', 'DZN', 'SET', 'XBG', 'XBX', 'XBE', 'XCT', 'XCS', 'XPK', 'XPA',"
            " 'XPL', 'XRO', 'GRM', 'KGM', 'LBR', 'MTR', 'FOT', 'LTR', 'GLL')",
            name=op.f("ck_products_unit_is_known"),
        ),
        sa.CheckConstraint("list_price >= 0", name=op.f("ck_products_list_price_not_negative")),
        sa.CheckConstraint("unit_cost >= 0", name=op.f("ck_products_unit_cost_not_negative")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_products_version_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["product_categories.tenant_id", "product_categories.id"],
            name=op.f("fk_products_tenant_id_category_id_product_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "currency"],
            ["tenants.id", "tenants.currency"],
            name=op.f("fk_products_tenant_id_currency_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_products_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_products_tenant_id_category_id"),
        "products",
        ["tenant_id", "category_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_products_tenant_id_name_id"), "products", ["tenant_id", "name", "id"], unique=False
    )
    op.create_index(
        op.f("ix_products_tenant_id_sku_id"), "products", ["tenant_id", "sku", "id"], unique=False
    )
    op.create_index(
        "uq_products_tenant_id_lower_sku",
        "products",
        ["tenant_id", sa.literal_column("lower(sku)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_products_tenant_id_lower_sku", table_name="products")
    op.drop_index(op.f("ix_products_tenant_id_sku_id"), table_name="products")
    op.drop_index(op.f("ix_products_tenant_id_name_id"), table_name="products")
    op.drop_index(op.f("ix_products_tenant_id_category_id"), table_name="products")
    op.drop_table("products")
    op.drop_constraint(op.f("uq_tenants_id_currency"), "tenants", type_="unique")
