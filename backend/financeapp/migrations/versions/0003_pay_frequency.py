"""pay frequency: how often each pay stub's paycheck comes, and its pay period

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paychecks", sa.Column("frequency", sa.String(length=16), nullable=True))
    op.add_column("paychecks", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("paychecks", sa.Column("period_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("paychecks", "period_end")
    op.drop_column("paychecks", "period_start")
    op.drop_column("paychecks", "frequency")
