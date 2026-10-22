"""unvested shares: stock plan shares granted but not vested, kept beside the vested holding without counting them

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("holdings_snapshots", sa.Column("unvested_quantity", sa.Numeric(24, 8), nullable=True))


def downgrade() -> None:
    op.drop_column("holdings_snapshots", "unvested_quantity")
