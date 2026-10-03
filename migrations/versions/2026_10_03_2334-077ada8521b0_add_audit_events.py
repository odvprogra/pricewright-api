"""Add audit events, append-only.

Revision ID: 077ada8521b0
Revises: dbd4548ac0e2
Create Date: 2026-10-03 23:34:16.986959+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "077ada8521b0"
down_revision: str | Sequence[str] | None = "dbd4548ac0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The history must not be rewritten (ADR-0013): any UPDATE or DELETE fails, whoever runs it.
# TRUNCATE stays possible for whoever owns the table (retention, test teardown).
_REJECT_CHANGES = """
CREATE FUNCTION reject_audit_event_changes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append-only' USING ERRCODE = 'restrict_violation';
END
$$
"""
_APPEND_ONLY = """
CREATE TRIGGER audit_events_are_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION reject_audit_event_changes()
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service_account')",
            name=op.f("ck_audit_events_actor_type_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_audit_events_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_audit_events_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_audit_events_tenant_id_actor_id_id"),
        "audit_events",
        ["tenant_id", "actor_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_events_tenant_id_resource_type_resource_id_id"),
        "audit_events",
        ["tenant_id", "resource_type", "resource_id", "id"],
        unique=False,
    )
    op.execute(_REJECT_CHANGES)
    op.execute(_APPEND_ONLY)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_are_append_only ON audit_events")
    op.execute("DROP FUNCTION reject_audit_event_changes()")
    op.drop_index(
        op.f("ix_audit_events_tenant_id_resource_type_resource_id_id"), table_name="audit_events"
    )
    op.drop_index(op.f("ix_audit_events_tenant_id_actor_id_id"), table_name="audit_events")
    op.drop_table("audit_events")
