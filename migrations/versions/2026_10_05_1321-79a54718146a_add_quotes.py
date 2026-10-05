"""Add quotes, their lines and approval requests, and the quote number counters.

Revision ID: 79a54718146a
Revises: 465e1d6bed42
Create Date: 2026-10-05 13:21:01.224267+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "79a54718146a"
down_revision: str | Sequence[str] | None = "465e1d6bed42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quote_number_counters",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "last_value >= 1", name=op.f("ck_quote_number_counters_last_value_positive")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_quote_number_counters_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "year", name=op.f("pk_quote_number_counters")),
    )
    op.create_table(
        "quotes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.SmallInteger(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_by_type", sa.String(length=20), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_by_type", sa.String(length=20), nullable=True),
        sa.Column("submitted_by_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("list_subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("net_subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tax_rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("tax", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("approval_threshold", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("priced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL)",
            name=op.f("ck_quotes_cancelled_with_a_reason"),
        ),
        sa.CheckConstraint(
            "(status = 'superseded') = (superseded_by_id IS NOT NULL)",
            name=op.f("ck_quotes_superseded_links_forward"),
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')",
            name=op.f("ck_quotes_created_by_type_is_known"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'sent', 'accepted', 'converted',"
            " 'rejected', 'cancelled', 'expired', 'superseded')",
            name=op.f("ck_quotes_status_is_known"),
        ),
        sa.CheckConstraint(
            "submitted_by_type IN ('user', 'service_account')",
            name=op.f("ck_quotes_submitted_by_type_is_known"),
        ),
        sa.CheckConstraint(
            "(revision = 1) = (supersedes_id IS NULL)",
            name=op.f("ck_quotes_later_revisions_link_back"),
        ),
        sa.CheckConstraint(
            "(submitted_by_type IS NULL) = (submitted_by_id IS NULL) "
            "AND (submitted_by_id IS NULL) = (submitted_at IS NULL)",
            name=op.f("ck_quotes_submitted_by_and_at_together"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_quotes_revision_positive")),
        sa.CheckConstraint("total = net_subtotal + tax", name=op.f("ck_quotes_total_adds_up")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_quotes_version_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "currency"],
            ["tenants.id", "tenants.currency"],
            name=op.f("fk_quotes_tenant_id_currency_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            name=op.f("fk_quotes_tenant_id_customer_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "superseded_by_id"],
            ["quotes.tenant_id", "quotes.id"],
            name=op.f("fk_quotes_tenant_id_superseded_by_id_quotes"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            ["quotes.tenant_id", "quotes.id"],
            name=op.f("fk_quotes_tenant_id_supersedes_id_quotes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotes")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_quotes_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "number", "revision", name=op.f("uq_quotes_tenant_id_number_revision")
        ),
        sa.UniqueConstraint(
            "tenant_id", "superseded_by_id", name=op.f("uq_quotes_tenant_id_superseded_by_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "supersedes_id", name=op.f("uq_quotes_tenant_id_supersedes_id")
        ),
    )
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_type", sa.String(length=20), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reasons", postgresql.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("discount", sa.Numeric(precision=21, scale=4), nullable=False),
        sa.Column("approval_threshold", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("list_subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("net_subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name=op.f("ck_approval_requests_closed_at_a_time"),
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_by IS NOT NULL)",
            name=op.f("ck_approval_requests_decided_by_a_person"),
        ),
        sa.CheckConstraint(
            "requested_by_type IN ('user', 'service_account')",
            name=op.f("ck_approval_requests_requested_by_type_is_known"),
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR comment IS NOT NULL",
            name=op.f("ck_approval_requests_rejection_explained"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name=op.f("ck_approval_requests_status_is_known"),
        ),
        sa.CheckConstraint(
            "cardinality(reasons) > 0", name=op.f("ck_approval_requests_has_reasons")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "currency"],
            ["tenants.id", "tenants.currency"],
            name=op.f("fk_approval_requests_tenant_id_currency_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "decided_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_approval_requests_tenant_id_decided_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            ["quotes.tenant_id", "quotes.id"],
            name=op.f("fk_approval_requests_tenant_id_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    op.create_index(
        op.f("ix_approval_requests_tenant_id_quote_id"),
        "approval_requests",
        ["tenant_id", "quote_id"],
        unique=False,
    )
    op.create_index(
        "uq_approval_requests_one_pending_per_quote",
        "approval_requests",
        ["tenant_id", "quote_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "quote_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
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
        sa.Column("added_by_type", sa.String(length=20), nullable=False),
        sa.Column("added_by_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "CASE override_kind "
            "WHEN 'rate' THEN override_rate IS NOT NULL AND override_unit_price IS NULL "
            "WHEN 'price' THEN override_unit_price IS NOT NULL AND override_rate IS NULL "
            "ELSE override_rate IS NULL AND override_unit_price IS NULL END "
            "AND (override_kind IS NULL) = (override_reason IS NULL) "
            "AND (override_kind IS NULL) = (override_by IS NULL)",
            name=op.f("ck_quote_lines_override_whole"),
        ),
        sa.CheckConstraint(
            "added_by_type IN ('user', 'service_account')",
            name=op.f("ck_quote_lines_added_by_type_is_known"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(steps) = 'array'", name=op.f("ck_quote_lines_steps_are_a_list")
        ),
        sa.CheckConstraint(
            "override_kind IN ('rate', 'price')", name=op.f("ck_quote_lines_override_kind_is_known")
        ),
        sa.CheckConstraint(
            "unit IN ('EA', 'PR', 'DZN', 'SET', 'XBG', 'XBX', 'XBE', 'XCT', 'XCS', 'XPK', 'XPA',"
            " 'XPL', 'XRO', 'GRM', 'KGM', 'LBR', 'MTR', 'FOT', 'LTR', 'GLL')",
            name=op.f("ck_quote_lines_unit_is_known"),
        ),
        sa.CheckConstraint(
            "(margin_floor_rule_id IS NULL) = (margin_floor_rate IS NULL) "
            "AND (margin_floor_rate IS NULL) = (margin_floor_label IS NULL)",
            name=op.f("ck_quote_lines_margin_floor_whole"),
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_quote_lines_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "currency"],
            ["tenants.id", "tenants.currency"],
            name=op.f("fk_quote_lines_tenant_id_currency_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name=op.f("fk_quote_lines_tenant_id_product_id_products"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            ["quotes.tenant_id", "quotes.id"],
            name=op.f("fk_quote_lines_tenant_id_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quote_lines")),
        sa.UniqueConstraint(
            "tenant_id",
            "quote_id",
            "position",
            name=op.f("uq_quote_lines_tenant_id_quote_id_position"),
        ),
    )


def downgrade() -> None:
    op.drop_table("quote_lines")
    op.drop_index(
        "uq_approval_requests_one_pending_per_quote",
        table_name="approval_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_approval_requests_tenant_id_quote_id"), table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_table("quotes")
    op.drop_table("quote_number_counters")
