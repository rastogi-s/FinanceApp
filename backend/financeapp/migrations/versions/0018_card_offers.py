"""card offers: Chase Offers and Capital One Offers on each card, read from the bank's offers page

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issuer", sa.String(16), nullable=False),
        sa.Column("merchant", sa.String(80), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("up_to", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_spend_cents", sa.BigInteger(), nullable=True),
        sa.Column("max_cents", sa.BigInteger(), nullable=True),
        sa.Column("channel", sa.String(8), nullable=True),
        sa.Column("expires", sa.Date(), nullable=True),
        sa.Column("soon", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("added", sa.Boolean(), nullable=True),
        sa.Column("first_seen", sa.Date(), nullable=False),
        sa.Column("seen", sa.Date(), nullable=False),
    )
    op.create_index("ix_card_offers_account_id", "card_offers", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_card_offers_account_id", table_name="card_offers")
    op.drop_table("card_offers")
