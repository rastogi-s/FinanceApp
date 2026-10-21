"""budget forecast: a starter heads-up rule for categories on pace to go over their budget

Revision ID: 0007
Revises: 0006
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

PARAMS = {"category_id": None, "forecast": True}


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
    # On for everyone, like the other starter rules; it can be changed or turned off in Settings → Alerts.
    op.bulk_insert(
        rules(), [{"kind": "budget", "params": PARAMS, "enabled": True, "state": {}, "created_at": datetime.now(UTC)}]
    )


def downgrade() -> None:
    t = rules()
    op.execute(t.delete().where(t.c.kind == "budget", t.c.params == PARAMS))
