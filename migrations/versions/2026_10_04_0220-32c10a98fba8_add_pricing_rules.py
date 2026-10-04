"""Add pricing rules, one row per rule, and the brackets of volume tiers.

Revision ID: 32c10a98fba8
Revises: 35c62dcb156a
Create Date: 2026-10-04 02:20:23.698911+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "32c10a98fba8"
down_revision: str | Sequence[str] | None = "35c62dcb156a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100, collation="unicode"), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("customer_tier", sa.String(length=10), nullable=True),
        sa.Column("rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
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
            "(customer_tier IS NOT NULL) = (kind = 'customer_tier')",
            name=op.f("ck_pricing_rules_customer_tier_only_on_tier_discounts"),
        ),
        sa.CheckConstraint(
            "(rate IS NULL) = (kind = 'volume_tier')",
            name=op.f("ck_pricing_rules_rate_unless_volume_tier"),
        ),
        sa.CheckConstraint(
            "CASE WHEN kind = 'margin_floor' THEN rate >= 0 AND rate < 1 "
            "ELSE rate > 0 AND rate <= 1 END",
            name=op.f("ck_pricing_rules_rate_in_range"),
        ),
        sa.CheckConstraint(
            "customer_tier IN ('standard', 'silver', 'gold')",
            name=op.f("ck_pricing_rules_customer_tier_is_known"),
        ),
        sa.CheckConstraint(
            "kind <> 'promotion' OR valid_to IS NOT NULL",
            name=op.f("ck_pricing_rules_promotions_end"),
        ),
        sa.CheckConstraint(
            "kind IN ('volume_tier', 'customer_tier', 'promotion', 'margin_floor')",
            name=op.f("ck_pricing_rules_kind_is_known"),
        ),
        sa.CheckConstraint(
            "product_id IS NULL OR category_id IS NULL", name=op.f("ck_pricing_rules_one_scope")
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=op.f("ck_pricing_rules_window_is_ordered"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_pricing_rules_version_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["product_categories.tenant_id", "product_categories.id"],
            name=op.f("fk_pricing_rules_tenant_id_category_id_product_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name=op.f("fk_pricing_rules_tenant_id_product_id_products"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_pricing_rules_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_rules")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_pricing_rules_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_pricing_rules_tenant_id_category_id"),
        "pricing_rules",
        ["tenant_id", "category_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pricing_rules_tenant_id_name_id"),
        "pricing_rules",
        ["tenant_id", "name", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pricing_rules_tenant_id_product_id"),
        "pricing_rules",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_table(
        "pricing_rule_brackets",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("min_quantity", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.CheckConstraint(
            "min_quantity > 0", name=op.f("ck_pricing_rule_brackets_min_quantity_positive")
        ),
        sa.CheckConstraint(
            "rate > 0 AND rate <= 1", name=op.f("ck_pricing_rule_brackets_rate_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["pricing_rules.tenant_id", "pricing_rules.id"],
            name=op.f("fk_pricing_rule_brackets_tenant_id_rule_id_pricing_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "rule_id", "min_quantity", name=op.f("pk_pricing_rule_brackets")
        ),
    )


def downgrade() -> None:
    op.drop_table("pricing_rule_brackets")
    op.drop_index(op.f("ix_pricing_rules_tenant_id_product_id"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_tenant_id_name_id"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_tenant_id_category_id"), table_name="pricing_rules")
    op.drop_table("pricing_rules")
