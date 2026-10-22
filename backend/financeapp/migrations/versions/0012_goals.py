"""goals: savings goals (the emergency fund is one), the accounts that fund them, and a starter milestone alert

Revision ID: 0012
Revises: 0011
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


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
    goals = op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("icon", sa.String(16), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("target_cents", sa.BigInteger(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("rate_percent", sa.Numeric(24, 8), nullable=True),
        sa.Column("monthly_cents", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "goal_funding",
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("cents", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_goal_funding_account_id", "goal_funding", ["account_id"])
    at = datetime.now(UTC)
    # The emergency fund is always a goal. With no accounts picked for it, it counts the cash no other goal has.
    op.bulk_insert(
        goals,
        [{"kind": "emergency", "name": "Emergency fund", "icon": "shield", "color": "teal", "priority": 1,
          "created_at": at}],
    )  # fmt: skip
    # On for everyone, like the other starter rules: every goal at 25%, 50%, 75% and 100%. Good news can wait for
    # a Focus to end, so these go out at normal priority rather than popping up.
    op.bulk_insert(
        rules(),
        [
            {
                "kind": "goal",
                "params": {"goal_id": None, "priority": "default"},
                "enabled": True,
                "state": {},
                "created_at": at,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_events WHERE kind = 'goal'")
    op.execute("DELETE FROM alert_rules WHERE kind = 'goal'")
    op.drop_index("ix_goal_funding_account_id", table_name="goal_funding")
    op.drop_table("goal_funding")
    op.drop_table("goals")
