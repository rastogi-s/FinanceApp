"""alerts and portfolio: quotes, alert rules and events, fund holdings for look-through exposure

Revision ID: 0005
Revises: 0004
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

# Two rules to start with, so alerts work without setting anything up. Both can be changed or turned off.
STARTER_RULES = [
    {"kind": "budget", "params": {"category_id": None, "percent": 90}},
    {"kind": "concentration", "params": {"percent": 10}},
]


def upgrade() -> None:
    op.create_table(
        "quotes",
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("previous_close", sa.Numeric(24, 8), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol"),
    )
    rules = op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=300), nullable=False),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key"),
    )
    op.create_index(op.f("ix_alert_events_fired_at"), "alert_events", ["fired_at"], unique=False)
    op.create_index(op.f("ix_alert_events_rule_id"), "alert_events", ["rule_id"], unique=False)
    op.create_table(
        "funds",
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("series_id", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("proxy_of", sa.String(length=40), nullable=True),
        sa.Column("accession", sa.String(length=24), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("equity_weight", sa.Numeric(24, 8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_table(
        "fund_constituents",
        sa.Column("fund_symbol", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("weight", sa.Numeric(14, 10), nullable=False),
        sa.Column("is_fund", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["fund_symbol"], ["funds.symbol"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fund_symbol", "key"),
    )
    op.create_table(
        "security_ids",
        sa.Column("cusip", sa.String(length=9), nullable=False),
        sa.Column("ticker", sa.String(length=40), nullable=True),
        sa.Column("cik", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("cusip"),
    )
    op.bulk_insert(
        rules,
        [{**r, "enabled": True, "state": {}, "created_at": datetime.now(UTC)} for r in STARTER_RULES],
    )


def downgrade() -> None:
    op.drop_table("security_ids")
    op.drop_table("fund_constituents")
    op.drop_table("funds")
    op.drop_index(op.f("ix_alert_events_rule_id"), table_name="alert_events")
    op.drop_index(op.f("ix_alert_events_fired_at"), table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
    op.drop_table("quotes")
