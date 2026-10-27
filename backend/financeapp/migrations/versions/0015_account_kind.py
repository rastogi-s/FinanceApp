"""account kind: set by hand when the kind guessed from type, subtype and name is wrong (a CD typed in as cash)

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("kind", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "kind")
