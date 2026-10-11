"""Add the transactional outbox: events stored with their change, for the worker (ADR-0008).

Revision ID: 71d9e90c03b2
Revises: eefefbd57f54
Create Date: 2026-10-11 18:01:04.596410+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "71d9e90c03b2"
down_revision: str | Sequence[str] | None = "eefefbd57f54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("aggregate_type", sa.String(length=30), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name=op.f("ck_outbox_events_delivered_when_delivered"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'dead')",
            name=op.f("ck_outbox_events_status_is_known"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_events_attempts_not_negative")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_outbox_events_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("position", name=op.f("uq_outbox_events_position")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_outbox_events_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_outbox_events_position"),
        "outbox_events",
        ["position"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        op.f("ix_outbox_events_tenant_id_aggregate_type_aggregate_id_position"),
        "outbox_events",
        ["tenant_id", "aggregate_type", "aggregate_id", "position"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_outbox_events_tenant_id_aggregate_type_aggregate_id_position"),
        table_name="outbox_events",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        op.f("ix_outbox_events_position"),
        table_name="outbox_events",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("outbox_events")
