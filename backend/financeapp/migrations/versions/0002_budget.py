"""budget: versioned budgets, pay stubs, essential categories, household preferences

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Flexible categories that count toward the emergency fund from the start (every fixed one does too).
ESSENTIAL = ["Groceries", "Transport", "Health"]


def upgrade() -> None:
    # The default stays, so an older version of the app (after going back with update.sh) still works.
    op.add_column("categories", sa.Column("essential", sa.Boolean(), nullable=False, server_default=sa.false()))
    categories = sa.table(
        "categories", sa.column("name", sa.String), sa.column("kind", sa.String), sa.column("essential", sa.Boolean)
    )
    op.execute(
        categories.update()
        .where(sa.or_(categories.c.kind == "fixed", categories.c.name.in_(ESSENTIAL)))
        .values(essential=True)
    )
    op.create_table(
        "budget_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "budget_lines",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("limit_cents", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["budget_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("version_id", "category_id"),
    )
    op.create_table(
        "paychecks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("gross_cents", sa.BigInteger(), nullable=False),
        sa.Column("taxes_cents", sa.BigInteger(), nullable=False),
        sa.Column("savings_cents", sa.BigInteger(), nullable=False),
        sa.Column("net_cents", sa.BigInteger(), nullable=False),
        sa.Column("employer_match_cents", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_paychecks_date"), "paychecks", ["date"], unique=False)
    op.create_table(
        "preferences",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("preferences")
    op.drop_index(op.f("ix_paychecks_date"), table_name="paychecks")
    op.drop_table("paychecks")
    op.drop_table("budget_lines")
    op.drop_table("budget_versions")
    op.drop_column("categories", "essential")
