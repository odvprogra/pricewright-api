"""Give customers an optional email, where the quotes sent to them go (ADR-0028).

Revision ID: c2c2107d54a4
Revises: 45fdfdf1114c
Create Date: 2026-10-12 16:12:52.244234+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2c2107d54a4"
down_revision: str | Sequence[str] | None = "45fdfdf1114c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("email", sa.String(length=254), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "email")
