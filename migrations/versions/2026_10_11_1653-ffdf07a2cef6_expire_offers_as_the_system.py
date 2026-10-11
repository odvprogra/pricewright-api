"""Let the system appear in the audit trail, and index the offers the expiry job looks for.

The worker's expiry job persists ``expired`` and audits it as the system (ADR-0026). Business tables
keep naming only people and integrations.

Revision ID: ffdf07a2cef6
Revises: decb40e51be3
Create Date: 2026-10-11 16:53:26.200492+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ffdf07a2cef6"
down_revision: str | Sequence[str] | None = "decb40e51be3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTOR_TYPE_IS_KNOWN = "ck_audit_events_actor_type_is_known"
_OFFERS_IN_FLIGHT = "status IN ('approved', 'pending_approval', 'sent')"


def upgrade() -> None:
    op.drop_constraint(op.f(_ACTOR_TYPE_IS_KNOWN), "audit_events", type_="check")
    op.create_check_constraint(
        op.f(_ACTOR_TYPE_IS_KNOWN),
        "audit_events",
        "actor_type IN ('user', 'service_account', 'system')",
    )
    op.create_index(
        op.f("ix_quotes_valid_until"),
        "quotes",
        ["valid_until"],
        unique=False,
        postgresql_where=sa.text(_OFFERS_IN_FLIGHT),
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quotes_valid_until"),
        table_name="quotes",
        postgresql_where=sa.text(_OFFERS_IN_FLIGHT),
    )
    op.drop_constraint(op.f(_ACTOR_TYPE_IS_KNOWN), "audit_events", type_="check")
    # NOT VALID: the system's events stay (audit events are append-only); new rows are checked.
    op.execute(
        f"ALTER TABLE audit_events ADD CONSTRAINT {_ACTOR_TYPE_IS_KNOWN} "
        "CHECK (actor_type IN ('user', 'service_account')) NOT VALID"
    )
