"""account terms: what a bank account pays and a CD's terms (rate, opened, term, maturity, penalty, renewal), moved
out of the "interest_accounts" preference where Dividends & interest kept the APY, maturity and term

Revision ID: 0017
Revises: 0016
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_terms",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("apy", sa.Numeric(6, 3), nullable=True),
        sa.Column("bank_apy", sa.Numeric(6, 3), nullable=True),
        sa.Column("opened", sa.Date(), nullable=True),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("maturity", sa.Date(), nullable=True),
        sa.Column("principal_cents", sa.BigInteger(), nullable=True),
        sa.Column("penalty_days", sa.Integer(), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=True),
        sa.Column("grace_days", sa.Integer(), nullable=True),
        sa.Column("pays_out", sa.Boolean(), nullable=True),
        sa.Column("monthly_fee_cents", sa.BigInteger(), nullable=True),
        sa.Column("min_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column("rates", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    conn = op.get_bind()
    saved = conn.execute(sa.text("SELECT value FROM preferences WHERE key = 'interest_accounts'")).scalar()
    if isinstance(saved, str):
        saved = json.loads(saved)
    known = {r[0] for r in conn.execute(sa.text("SELECT id FROM accounts"))}
    for key, entry in (saved or {}).items():
        if not key.isdigit() or int(key) not in known or not isinstance(entry, dict):
            continue
        conn.execute(
            sa.text(
                "INSERT INTO account_terms (account_id, apy, maturity, term_months) "
                "VALUES (:id, :apy, CAST(:maturity AS date), :term)"
            ),
            {
                "id": int(key),
                "apy": entry.get("apy"),
                "maturity": entry.get("maturity"),
                "term": entry.get("term_months"),
            },
        )
    conn.execute(sa.text("DELETE FROM preferences WHERE key = 'interest_accounts'"))


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT account_id, apy, maturity, term_months FROM account_terms"))
    value = {
        str(r.account_id): {
            "apy": float(r.apy) if r.apy is not None else None,
            "maturity": r.maturity.isoformat() if r.maturity else None,
            "term_months": r.term_months,
        }
        for r in rows
        if r.apy is not None or r.maturity is not None or r.term_months is not None
    }
    if value:
        conn.execute(
            sa.text("INSERT INTO preferences (key, value) VALUES ('interest_accounts', CAST(:v AS jsonb))"),
            {"v": json.dumps(value)},
        )
    op.drop_table("account_terms")
