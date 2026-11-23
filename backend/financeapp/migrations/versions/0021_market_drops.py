"""market drops: an index's record close on its quote, and a starter alert for the S&P 500 falling from its high

Revision ID: 0021
Revises: 0020
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# On for everyone, like the other starter rules: the S&P 500 5% under its record close, then 10%, 15%, 20%...
# Changed or turned off in Settings → Alerts.
STARTER = {"symbol": "^GSPC", "step": 5, "priority": "high"}


def upgrade() -> None:
    op.add_column("quotes", sa.Column("peak", sa.Numeric(24, 8), nullable=True))
    op.add_column("quotes", sa.Column("peak_on", sa.Date(), nullable=True))
    op.add_column("quotes", sa.Column("peak_full", sa.Boolean(), nullable=False, server_default=sa.false()))
    rules = sa.table(
        "alert_rules",
        sa.column("kind", sa.String),
        sa.column("params", postgresql.JSONB),
        sa.column("enabled", sa.Boolean),
        sa.column("state", postgresql.JSONB),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        rules, [{"kind": "market", "params": STARTER, "enabled": True, "state": {}, "created_at": datetime.now(UTC)}]
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_events WHERE kind = 'market'")
    op.execute("DELETE FROM alert_rules WHERE kind = 'market'")
    for name in ("peak_full", "peak_on", "peak"):
        op.drop_column("quotes", name)
