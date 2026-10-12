"""Index delivered outbox events by delivery time, for the worker's purge (ADR-0008).

Revision ID: 45fdfdf1114c
Revises: 71d9e90c03b2
Create Date: 2026-10-12 15:37:55.917906+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "45fdfdf1114c"
down_revision: str | Sequence[str] | None = "71d9e90c03b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_outbox_events_delivered_at"),
        "outbox_events",
        ["delivered_at"],
        unique=False,
        postgresql_where=sa.text("status = 'delivered'"),
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_outbox_events_delivered_at"),
        table_name="outbox_events",
        postgresql_where=sa.text("status = 'delivered'"),
    )
