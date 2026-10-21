"""price trends: each quoted symbol's 52-week high and low and its 50- and 200-day averages, and starter dip rules

Revision ID: 0008
Revises: 0007
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# On for everyone, like the other starter rules: every stock and fund held, 10% under its 52-week high (then 20%,
# 30%...), and dropping under its 200-day average. Both can be changed or turned off in Settings → Alerts.
STARTER = [{"symbol": None, "from_high_percent": 10}, {"symbol": None, "below_average": 200}]


def rules() -> sa.Table:
    return sa.table(
        "alert_rules",
        sa.column("kind", sa.String),
        sa.column("params", postgresql.JSONB),
        sa.column("enabled", sa.Boolean),
        sa.column("state", postgresql.JSONB),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    for name in ("year_high", "year_low", "average_50", "average_200"):
        op.add_column("quotes", sa.Column(name, sa.Numeric(24, 8), nullable=True))
    op.add_column("quotes", sa.Column("trend_as_of", sa.Date(), nullable=True))
    at = datetime.now(UTC)
    op.bulk_insert(
        rules(), [{"kind": "dip", "params": p, "enabled": True, "state": {}, "created_at": at} for p in STARTER]
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_events WHERE kind = 'dip'")
    op.execute("DELETE FROM alert_rules WHERE kind = 'dip'")
    for name in ("trend_as_of", "average_200", "average_50", "year_low", "year_high"):
        op.drop_column("quotes", name)
