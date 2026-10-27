"""credit cards: which card product each card account is, its rates and statement, and what the card network saw on
each purchase (merchant category code, online or in store, website), which card rewards go by

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("mcc", sa.String(4), nullable=True))
    op.add_column("transactions", sa.Column("channel", sa.String(8), nullable=True))
    op.add_column("transactions", sa.Column("website", sa.String(120), nullable=True))
    op.add_column("accounts", sa.Column("credit_limit_cents", sa.BigInteger(), nullable=True))
    op.create_table(
        "card_links",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("product", sa.String(60), nullable=False),
        sa.Column("choice", sa.String(32), nullable=True),
        sa.Column("role", sa.String(12), nullable=False, server_default="active"),
        sa.Column("renewal_month", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "card_statements",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("purchase_apr", sa.Numeric(8, 4), nullable=True),
        sa.Column("aprs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("statement_cents", sa.BigInteger(), nullable=True),
        sa.Column("statement_date", sa.Date(), nullable=True),
        sa.Column("minimum_cents", sa.BigInteger(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("last_payment_cents", sa.BigInteger(), nullable=True),
        sa.Column("last_payment_date", sa.Date(), nullable=True),
        sa.Column("overdue", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("card_statements")
    op.drop_table("card_links")
    op.drop_column("accounts", "credit_limit_cents")
    op.drop_column("transactions", "website")
    op.drop_column("transactions", "channel")
    op.drop_column("transactions", "mcc")
