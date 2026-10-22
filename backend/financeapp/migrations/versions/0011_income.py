"""income: brokerage activity (dividends, interest, reinvestments) and each held symbol's dividend history

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investment_transactions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("subtype", sa.String(length=48), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=True),
        sa.Column("price", sa.Numeric(24, 8), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "external_id"),
    )
    op.create_index("ix_investment_transactions_account_id", "investment_transactions", ["account_id"])
    op.create_index("ix_investment_transactions_date", "investment_transactions", ["date"])
    op.create_table(
        "dividend_history",
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "ex_date"),
    )


def downgrade() -> None:
    op.drop_table("dividend_history")
    op.drop_index("ix_investment_transactions_date", table_name="investment_transactions")
    op.drop_index("ix_investment_transactions_account_id", table_name="investment_transactions")
    op.drop_table("investment_transactions")
