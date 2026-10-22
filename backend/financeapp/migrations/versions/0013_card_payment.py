"""card payment: a "Credit card payment" category, so paying a card shows as that instead of a plain transfer

It's a transfer kind, so it never counts as spending or income. Existing card payments move into it the next time
transfers are sorted (at startup after the update).

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

NAME = "Credit card payment"


def upgrade() -> None:
    bind = op.get_bind()
    sort = bind.execute(sa.text("select sort from categories where name = 'Transfer'")).scalar()
    if sort is None:
        sort = bind.execute(sa.text("select coalesce(max(sort), -1) + 1 from categories")).scalar()
    else:
        bind.execute(sa.text("update categories set sort = sort + 1 where sort >= :s"), {"s": sort})
    bind.execute(
        sa.text("insert into categories (name, kind, sort) values (:n, 'transfer', :s) on conflict (name) do nothing"),
        {"n": NAME, "s": sort},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "update transactions set category_id = (select id from categories where name = 'Transfer') "
            "where category_id = (select id from categories where name = :n)"
        ),
        {"n": NAME},
    )
    bind.execute(sa.text("delete from categories where name = :n"), {"n": NAME})
