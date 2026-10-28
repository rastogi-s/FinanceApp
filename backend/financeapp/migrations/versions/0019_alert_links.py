"""alert links: the page each alert is about, opened by tapping the push

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_events", sa.Column("link", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_events", "link")
