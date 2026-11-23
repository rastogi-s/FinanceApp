"""vix and yields: starter alerts for the VIX reaching 25, 30 and 40, and for the 10-year Treasury yield above 5% or an
inverted yield curve

Revision ID: 0022
Revises: 0021
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# On for everyone, like the other starter rules; changed or turned off in Settings → Alerts. Yields move slowly and are
# checked once a day, so they come as a normal banner rather than breaking through a Focus.
STARTER = [
    ("vix", {"levels": [25, 30, 40], "priority": "high"}),
    ("yield", {"above": 5.0, "inversion": True, "priority": "default"}),
]


def upgrade() -> None:
    rules = sa.table(
        "alert_rules",
        sa.column("kind", sa.String),
        sa.column("params", postgresql.JSONB),
        sa.column("enabled", sa.Boolean),
        sa.column("state", postgresql.JSONB),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    at = datetime.now(UTC)
    op.bulk_insert(
        rules, [{"kind": k, "params": p, "enabled": True, "state": {}, "created_at": at} for k, p in STARTER]
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_events WHERE kind IN ('vix', 'yield')")
    op.execute("DELETE FROM alert_rules WHERE kind IN ('vix', 'yield')")
