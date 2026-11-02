"""budget month: the month a transaction counts toward, when picked by hand

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("budget_month", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "budget_month")
