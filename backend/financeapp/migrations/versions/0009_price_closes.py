"""price closes: the last month of each quoted symbol's daily closes, for the sparkline in the holdings table

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quotes", sa.Column("closes", postgresql.JSONB(), nullable=True))
    # Look each symbol's year of closes up again on the next round, so the sparklines fill in today.
    op.execute("UPDATE quotes SET trend_as_of = NULL")


def downgrade() -> None:
    op.drop_column("quotes", "closes")
