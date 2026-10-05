"""Add orders and their lines, link converted quotes to them, and count numbers per series.

Orders are converted once from accepted quotes and copy their snapshot (ADR-0023). The quote
number counters become document number counters with a series (quote or order), keeping every
quote count (ADR-0021).

Revision ID: 837c6f4483f3
Revises: 11744420ea5c
Create Date: 2026-10-05 22:26:29.560320+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "837c6f4483f3"
down_revision: str | Sequence[str] | None = "11744420ea5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNITS = (
    "unit IN ('EA', 'PR', 'DZN', 'SET', 'XBG', 'XBX', 'XBE', 'XCT', 'XCS', 'XPK', 'XPA', "
    "'XPL', 'XRO', 'GRM', 'KGM', 'LBR', 'MTR', 'FOT', 'LTR', 'GLL')"
)
_MARGIN_FLOOR_WHOLE = (
    "(margin_floor_rule_id IS NULL) = (margin_floor_rate IS NULL) "
    "AND (margin_floor_rate IS NULL) = (margin_floor_label IS NULL)"
)
_OVERRIDE_WHOLE = (
    "CASE override_kind "
    "WHEN 'rate' THEN override_rate IS NOT NULL AND override_unit_price IS NULL "
    "WHEN 'price' THEN override_unit_price IS NOT NULL AND override_rate IS NULL "
    "ELSE override_rate IS NULL AND override_unit_price IS NULL END "
    "AND (override_kind IS NULL) = (override_reason IS NULL) "
    "AND (override_kind IS NULL) = (override_by IS NULL)"
)
_COUNTER_CONSTRAINTS = (
    ("pk_quote_number_counters", "pk_document_number_counters"),
    ("fk_quote_number_counters_tenant_id_tenants", "fk_document_number_counters_tenant_id_tenants"),
    (
        "ck_quote_number_counters_last_value_positive",
        "ck_document_number_counters_last_value_positive",
    ),
    # PostgreSQL 18 names NOT NULL constraints after their table too.
    *(
        (f"quote_number_counters_{column}_not_null", f"document_number_counters_{column}_not_null")
        for column in ("tenant_id", "year", "last_value")
    ),
)


def _count_numbers_per_series() -> None:
    op.rename_table("quote_number_counters", "document_number_counters")
    for old, new in _COUNTER_CONSTRAINTS:
        op.execute(f"ALTER TABLE document_number_counters RENAME CONSTRAINT {old} TO {new}")
    op.add_column(
        "document_number_counters",
        sa.Column("series", sa.String(length=10), server_default="quote", nullable=False),
    )
    op.alter_column("document_number_counters", "series", server_default=None)
    op.drop_constraint(op.f("pk_document_number_counters"), "document_number_counters")
    op.create_primary_key(
        op.f("pk_document_number_counters"),
        "document_number_counters",
        ["tenant_id", "series", "year"],
    )
    op.create_check_constraint(
        op.f("ck_document_number_counters_series_is_known"),
        "document_number_counters",
        "series IN ('quote', 'order')",
    )


def _count_quote_numbers_only() -> None:
    op.execute("DELETE FROM document_number_counters WHERE series <> 'quote'")
    op.drop_constraint(
        op.f("ck_document_number_counters_series_is_known"), "document_number_counters"
    )
    op.drop_constraint(op.f("pk_document_number_counters"), "document_number_counters")
    op.create_primary_key(
        op.f("pk_document_number_counters"), "document_number_counters", ["tenant_id", "year"]
    )
    op.drop_column("document_number_counters", "series")
    for old, new in _COUNTER_CONSTRAINTS:
        op.execute(f"ALTER TABLE document_number_counters RENAME CONSTRAINT {new} TO {old}")
    op.rename_table("document_number_counters", "quote_number_counters")


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(length=24), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("quote_number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("customer_account_number", sa.String(length=20), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("customer_tax_id", sa.String(length=30), nullable=True),
        sa.Column("customer_payment_terms_days", sa.SmallInteger(), nullable=False),
        sa.Column("customer_reference", sa.String(length=35), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("list_subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("net_subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tax_rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("tax", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("priced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column("created_by_type", sa.String(length=20), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL)",
            name=op.f("ck_orders_cancelled_with_a_reason"),
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')",
            name=op.f("ck_orders_created_by_type_is_known"),
        ),
        sa.CheckConstraint(
            "customer_reference <> ''", name=op.f("ck_orders_customer_reference_not_empty")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'cancelled')", name=op.f("ck_orders_status_is_known")
        ),
        sa.CheckConstraint(
            "customer_payment_terms_days BETWEEN 0 AND 365",
            name=op.f("ck_orders_payment_terms_in_range"),
        ),
        sa.CheckConstraint("total = net_subtotal + tax", name=op.f("ck_orders_total_adds_up")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_orders_version_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "currency"],
            ["tenants.id", "tenants.currency"],
            name=op.f("fk_orders_tenant_id_currency_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            name=op.f("fk_orders_tenant_id_customer_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            ["quotes.tenant_id", "quotes.id"],
            name=op.f("fk_orders_tenant_id_quote_id_quotes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_orders_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "number", name=op.f("uq_orders_tenant_id_number")),
        sa.UniqueConstraint("tenant_id", "quote_id", name=op.f("uq_orders_tenant_id_quote_id")),
    )
    op.create_table(
        "order_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=40), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=3), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("list_unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("list_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("net_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("cost_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("margin_floor_rule_id", sa.Uuid(), nullable=True),
        sa.Column("margin_floor_label", sa.String(length=100), nullable=True),
        sa.Column("margin_floor_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("override_kind", sa.String(length=5), nullable=True),
        sa.Column("override_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("override_unit_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("override_reason", sa.String(length=200), nullable=True),
        sa.Column("override_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(_OVERRIDE_WHOLE, name=op.f("ck_order_lines_override_whole")),
        sa.CheckConstraint(
            "jsonb_typeof(steps) = 'array'", name=op.f("ck_order_lines_steps_are_a_list")
        ),
        sa.CheckConstraint(
            "override_kind IN ('rate', 'price')", name=op.f("ck_order_lines_override_kind_is_known")
        ),
        sa.CheckConstraint(_UNITS, name=op.f("ck_order_lines_unit_is_known")),
        sa.CheckConstraint(_MARGIN_FLOOR_WHOLE, name=op.f("ck_order_lines_margin_floor_whole")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_lines_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "currency"],
            ["tenants.id", "tenants.currency"],
            name=op.f("fk_order_lines_tenant_id_currency_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name=op.f("fk_order_lines_tenant_id_order_id_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name=op.f("fk_order_lines_tenant_id_product_id_products"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_lines")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_order_lines_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "position",
            name=op.f("uq_order_lines_tenant_id_order_id_position"),
        ),
    )
    op.add_column("quotes", sa.Column("order_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_quotes_tenant_id_order_id"), "quotes", ["tenant_id", "order_id"]
    )
    op.create_foreign_key(
        op.f("fk_quotes_tenant_id_order_id_orders"),
        "quotes",
        "orders",
        ["tenant_id", "order_id"],
        ["tenant_id", "id"],
        initially="DEFERRED",
        deferrable=True,
    )
    # Autogenerate does not detect check constraints on an existing table.
    op.create_check_constraint(
        op.f("ck_quotes_converted_links_its_order"),
        "quotes",
        "(status = 'converted') = (order_id IS NOT NULL)",
    )
    _count_numbers_per_series()


def downgrade() -> None:
    _count_quote_numbers_only()
    op.drop_constraint(op.f("ck_quotes_converted_links_its_order"), "quotes", type_="check")
    op.drop_constraint(op.f("fk_quotes_tenant_id_order_id_orders"), "quotes", type_="foreignkey")
    op.drop_constraint(op.f("uq_quotes_tenant_id_order_id"), "quotes", type_="unique")
    op.drop_column("quotes", "order_id")
    op.drop_table("order_lines")
    op.drop_table("orders")
