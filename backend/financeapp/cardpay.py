"""Whether each card's latest statement is paid, and the push before it's due when it isn't.

A card's statement (CardStatement, from Plaid's liabilities) says what was owed when it closed and when it's due.
What's been paid since comes from the card's own activity: money arriving on the card after the statement date that
is a payment (in Credit card payment, a matched transfer, or worded like one), not a refund. The bank's own "last
payment" counts too when it's after the statement, for a payment that hasn't shown up as a transaction yet. And a
balance owed (from a sync after the statement) that's smaller than what's left means it's been paid down to that.

  paid     nothing left of the statement
  partial  some paid, some left
  due      nothing paid yet
  late     the due date has gone by with some left

Pushes go through the alerts queue (alerts.send_pending): quiet hours, grouping and the ntfy/email fallback all
apply, at high priority so it pops up. One push SOON_DAYS before the due date, one on the day and one the day after
if it's still not paid, each once per statement (the dedup key names the card, the due date and the step). Only the
latest step that applies goes out, so a phone that was off for days doesn't get three at once. Once the statement
is paid, any of them still waiting (quiet hours) are dropped. The push names the card and the day, never an amount.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import transfers
from financeapp.models import AlertEvent, Balance, CardLink, CardStatement, Category, Transaction, now

SOON_DAYS = 3
LATE_DAYS = 3  # how long after the due date it still says "late" (the bank moves the due date on after that)
NO_DATE_DAYS = 30  # a statement with no date: payments in the month before the due date


@dataclass
class Payment:
    state: str  # paid | partial | due | late
    statement_cents: int
    paid_cents: int
    left_cents: int
    due_date: date

    def json(self) -> dict:
        return {
            "state": self.state,
            "statement_cents": self.statement_cents,
            "paid_cents": self.paid_cents,
            "left_cents": self.left_cents,
            "due_date": self.due_date.isoformat(),
        }


def since(s: CardStatement) -> date:
    """The day after which a payment counts against this statement."""
    return s.statement_date or s.due_date - timedelta(days=NO_DATE_DAYS)


def is_payment(t: Transaction, card_category: int | None) -> bool:
    if t.amount_cents <= 0 or transfers._looks_like_refund(t):
        return False
    if card_category is not None and t.category_id == card_category:
        return True
    return (
        t.transfer_group_id is not None
        or t.provider_category == transfers.CARD_CODE
        or bool(transfers.CARD_TEXT.search(f"{t.merchant} {t.description}"))
    )


def work_out(s: CardStatement, paid_cents: int, owed_now: int | None, today: date) -> Payment | None:
    """The statement's payment state, from what's been paid since it and the balance owed now (None: not known, or
    from before the statement)."""
    if not s.due_date or not s.statement_cents or s.statement_cents <= 0:
        return None
    after = since(s)
    if s.last_payment_cents and s.last_payment_date and s.last_payment_date > after:
        paid_cents = max(paid_cents, s.last_payment_cents)
    left = max(s.statement_cents - paid_cents, 0)
    if owed_now is not None:
        left = max(min(left, owed_now), 0)
    if left == 0:
        state = "paid"
    elif today > s.due_date:
        state = "late"
    else:
        state = "partial" if paid_cents > 0 else "due"
    return Payment(state, s.statement_cents, min(paid_cents, s.statement_cents), left, s.due_date)


def load(db: DbSession, today: date, account_ids: list[int] | None = None) -> dict[int, Payment]:
    """Each card's latest statement with its payment state, by account id."""
    q = select(CardStatement)
    if account_ids is not None:
        q = q.where(CardStatement.account_id.in_(account_ids))
    statements = [s for s in db.scalars(q) if s.due_date and s.statement_cents and s.statement_cents > 0]
    if not statements:
        return {}
    card_category = db.scalar(select(Category.id).where(Category.name == transfers.CARD_CATEGORY))
    earliest = min(since(s) for s in statements)
    ids = [s.account_id for s in statements]
    paid: dict[int, int] = {}
    starts = {s.account_id: since(s) for s in statements}
    for t in db.scalars(
        select(Transaction).where(
            Transaction.account_id.in_(ids), Transaction.date > earliest, Transaction.amount_cents > 0
        )
    ):
        if t.date > starts[t.account_id] and is_payment(t, card_category):
            paid[t.account_id] = paid.get(t.account_id, 0) + t.amount_cents
    out = {}
    for s in statements:
        newest = select(Balance).where(Balance.account_id == s.account_id).order_by(Balance.date.desc()).limit(1)
        bal = db.scalar(newest)
        owed = bal.current_cents if bal is not None and bal.date > since(s) else None
        p = work_out(s, paid.get(s.account_id, 0), owed, today)
        if p is not None:
            out[s.account_id] = p
    return out


# ---- the push before it's due


def step(p: Payment, today: date) -> str | None:
    """The latest push step that applies today, or None."""
    if p.state == "paid":
        return None
    days = (p.due_date - today).days
    if 1 <= days <= SOON_DAYS:
        return "soon"
    if days == 0:
        return "today"
    if -LATE_DAYS <= days < 0:
        return "late"
    return None


def wording(name: str, p: Payment, stage: str, today: date) -> tuple[str, str]:
    """A push's title and body: the card and the day, never an amount."""
    days = (p.due_date - today).days
    when = "tomorrow" if days == 1 else f"{p.due_date:%A}"
    if stage == "soon":
        title = f"{name} payment due {when}"
    elif stage == "today":
        title = f"{name} payment due today"
    else:
        title = f"{name} payment is past due"
    if p.state == "partial":
        body = "Part of the statement is paid. Tap to see what's left."
    elif stage == "late":
        body = f"It was due {p.due_date:%b} {p.due_date.day} and isn't paid in full yet. Tap to see what's left."
    else:
        body = "The statement isn't paid yet. Tap to see how much."
    return title, body


def key(account_id: int, due: date, stage: str = "") -> str:
    return f"card-due:{account_id}:{due.isoformat()}:{stage}"


def queue(db: DbSession, today: date) -> int:
    """Saves the push that's due for each unpaid card statement; drops any still waiting for a statement since paid.
    Returns how many were added."""
    from financeapp import cards, household  # cards and household are heavy; only when it runs

    accounts = [a for a in household.counted(db) if a.type == "credit"]
    if not accounts:
        return 0
    payments = load(db, today, [a.id for a in accounts])
    links = {link.account_id: link for link in db.scalars(select(CardLink).where(CardLink.account_id.in_(payments)))}
    names = {a.id: cards.label_of(a, links.get(a.id)) for a in accounts}
    added = 0
    for account_id, p in payments.items():
        if p.state == "paid":
            db.execute(
                delete(AlertEvent).where(
                    AlertEvent.dedup_key.startswith(key(account_id, p.due_date)),
                    AlertEvent.status.in_(["pending", "held"]),
                )
            )
            continue
        stage = step(p, today)
        if stage is None:
            continue
        title, body = wording(names.get(account_id) or "Your card", p, stage, today)
        stmt = insert(AlertEvent).values(
            rule_id=None, kind="card", title=title[:120], body=body[:300], dedup_key=key(account_id, p.due_date, stage),
            fired_at=now(), status="pending", attempts=0, link=f"/cards?card={account_id}",
        )  # fmt: skip
        if db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None:
            added += 1
    return added
