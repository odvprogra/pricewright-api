"""Count failed logins.

Revision ID: 87506955f1ab
Revises: d98f67df2938
Create Date: 2026-10-03 15:58:02.083304+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "87506955f1ab"
down_revision: str | Sequence[str] | None = "d98f67df2938"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    # Autogenerate does not detect check constraints.
    op.create_check_constraint(
        op.f("ck_users_failed_login_attempts_not_negative"), "users", "failed_login_attempts >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_failed_login_attempts_not_negative"), "users", type_="check")
    op.drop_column("users", "failed_login_attempts")
