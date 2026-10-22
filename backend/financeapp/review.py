"""Check categories: what the app isn't sure about, grouped so one answer sorts many, and how much to trust the rest.

The budget and the savings rate are only as right as the categories under them. Four things are worth a look, from
the last three full months and this one:

- no_category: money out that nothing recognised ("Uncategorized"), so it counts as spending of no particular kind.
- guess: a loose word ("grill", "water") or one of Plaid's broadest groups picked the category.
- sent: a one-sided transfer out that isn't a card payment, a brokerage or the household's own name: a Zelle to a
  sitter or a transfer to someone else's account. It's left out of spending, so if it was really spending the
  savings rate is too high.
- money_in / refund: money in that nothing recognised. "Other income" raises the savings rate; a refund lowers
  spending. Deposits from whoever pays the household are pay and aren't asked about.

Transactions are grouped by merchant (store numbers aside), or by the person for Zelle and Venmo. Answering a group
files every transaction in it by hand and, unless it's a transfer, makes a rule so later ones go the same way.

The health score is the share of the money in that window (spending, money in, transfers that aren't certain) whose
category is trusted: picked by hand, a rule, a brand, Plaid's specific category, or a transfer both sides of which
were seen or that pays a card, a brokerage or the household's own name.
"""

import re
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, transfers
from financeapp.categorize import PERSON, categorize, merchant_key, normalize_merchant, sender_of
from financeapp.models import Category, MerchantRule, Paycheck, Transaction

MONTHS = 3  # full months looked at, plus this one
MAX_GROUPS = 200
EXAMPLES = 3
REASONS = ("no_category", "guess", "sent", "money_in", "refund")
CARD_TEXT = re.compile(transfers.CARD_PAYMENT, re.IGNORECASE)
BROKERAGE = re.compile(transfers.BROKERAGES, re.IGNORECASE)


class Look:
    """Everything counted in the window, with what's needed to say which transactions are in doubt."""

    def __init__(self, db: DbSession) -> None:
        self.db = db
        self.accounts = {a.id: a for a in budget.counted_accounts(db)}
        self.since = budget.add_months(budget.month_start(budget.today()), -MONTHS)
        self.ledger = budget.Ledger(db, list(self.accounts.values()), self.since)
        self.txns = [t for t in self.ledger.txns if not t.pending]
        recent = db.scalars(select(Paycheck).order_by(Paycheck.date.desc(), Paycheck.id.desc()).limit(12)).all()
        stubs = [(p.date, p.net_cents) for p in recent]
        self.payers = budget.pay_merchants(self.ledger, stubs, budget.preferences(db), budget.latest_splits(recent))
        self.names = transfers.own_names(db)

    def category(self, t: Transaction) -> Category | None:
        return self.ledger.categories.get(t.category_id) if t.category_id else None

    def certain_transfer(self, t: Transaction) -> str | None:
        """For a one-sided transfer that's surely the household's own money: card, invest or own. None otherwise."""
        text = f"{t.merchant} {t.description}"
        if self.ledger.is_card_payment(t) or CARD_TEXT.search(text):
            return "card"
        if self.accounts[t.account_id].type in ("credit", "loan"):
            return "card" if self.accounts[t.account_id].type == "credit" else "own"
        if BROKERAGE.search(text):
            return "invest"
        if transfers.is_own(t, self.names):
            return "own"
        if t.amount_cents > 0:
            return "own"  # money in from an account that isn't linked: leaving it out never inflates savings
        return None

    def doubt(self, t: Transaction) -> str | None:
        if t.category_source in ("user", "rule"):
            return None
        kind = self.ledger.kind(t)
        if kind == "transfer":
            return None if self.certain_transfer(t) else "sent"
        if t.category_source == "guess":
            return "guess"
        if t.category_source != "none":
            return None
        if t.amount_cents < 0:
            return "no_category"
        if budget.is_pay_deposit(self.ledger, t, self.payers):
            return None
        return "money_in" if kind == "income" else "refund"


def group_key(t: Transaction, reason: str) -> tuple[str, str | None]:
    who = sender_of(t)
    if who:
        return f"{reason}:{PERSON}{who}", who
    return f"{reason}:{merchant_key(t.merchant) or normalize_merchant(t.description)}", None


def _cents_out(t: Transaction) -> int:
    return abs(t.amount_cents)


def state(db: DbSession) -> dict:
    """The health score, what's never counted as spending, and the groups to check, biggest first."""
    look = Look(db)
    groups: dict[str, dict] = {}
    members: dict[str, list[Transaction]] = defaultdict(list)
    base = doubtful = 0
    for t in look.txns:
        reason = look.doubt(t)
        if look.ledger.kind(t) == "transfer" and reason is None:
            continue  # the household's own money moving: not part of the score
        base += _cents_out(t)
        if reason is None:
            continue
        doubtful += _cents_out(t)
        key, who = group_key(t, reason)
        members[key].append(t)
        if key not in groups:
            groups[key] = {"key": key, "reason": reason, "person": who.title() if who else None}

    rows = []
    for key, g in groups.items():
        ts = sorted(members[key], key=lambda t: (t.date, t.id), reverse=True)
        c = look.category(ts[0])
        merchant = Counter(t.merchant for t in ts).most_common(1)[0][0]
        rows.append(
            g
            | {
                "merchant": merchant,
                "count": len(ts),
                "total_cents": sum(t.amount_cents for t in ts),
                "category_id": c.id if c else None,
                "category": c.name if c else "Uncategorized",
                "last_date": ts[0].date.isoformat(),
                "ids": [str(t.id) for t in ts],
                "examples": [
                    {
                        "date": t.date.isoformat(),
                        "amount_cents": t.amount_cents,
                        "description": t.description,
                        "account": look.accounts[t.account_id].name,
                    }
                    for t in ts[:EXAMPLES]
                ],
            }
        )
    rows.sort(key=lambda r: (-abs(r["total_cents"]), r["merchant"]))
    items = sum(r["count"] for r in rows)
    return {
        "since": look.since.isoformat(),
        "score": round((base - doubtful) / base * 100) if base else None,
        "checked_cents": base,
        "doubtful_cents": doubtful,
        "items": items,
        "kept_out": kept_out(db, look),
        "groups": rows[:MAX_GROUPS],
    }


def kept_out(db: DbSession, look: Look) -> dict:
    """Money moved between the household's own accounts in the window, which never counts as spending or income:
    card payments, moves into investments, and other moves between accounts (money out of the paying side)."""
    out = {"card": 0, "invest": 0, "own": 0}
    rows = db.scalars(
        select(Transaction).where(
            Transaction.account_id.in_(list(look.accounts)),
            Transaction.date >= look.since,
            Transaction.transfer_group_id.is_not(None),
        )
    ).all()
    sides: dict = defaultdict(list)
    for t in rows:
        sides[t.transfer_group_id].append(t)
    for pair in sides.values():
        paying = [t for t in pair if t.amount_cents < 0]
        if not paying:
            continue
        types = {look.accounts[t.account_id].type for t in pair}
        kind = "card" if "credit" in types or any(look.ledger.is_card_payment(t) for t in pair) else None
        kind = kind or ("invest" if "investment" in types else "own")
        out[kind] -= sum(t.amount_cents for t in paying)
    for t in look.txns:
        if look.ledger.kind(t) == "transfer" and t.amount_cents < 0 and (kind := look.certain_transfer(t)):
            out[kind] -= t.amount_cents
    return {"card_cents": out["card"], "invest_cents": out["invest"], "own_cents": out["own"]}


def items(db: DbSession) -> int:
    return state(db)["items"]


# ---- answering


class NotFound(Exception):
    pass


def apply(db: DbSession, ids: list[int], category_id: int, remember: bool, user_id: int | None) -> int:
    """Files these transactions under a category by hand. With `remember` (and a category that isn't a transfer),
    later ones from the same merchant or person go there too, and so does their history. A Zelle or Venmo person
    filed under Transfer becomes one of the household's own names. Returns how many others changed with them."""
    category = db.get(Category, category_id)
    txns = list(db.scalars(select(Transaction).where(Transaction.id.in_(ids)))) if ids else []
    if category is None or not txns:
        raise NotFound
    for t in txns:
        if category.kind != "transfer":
            transfers.unmatch(db, t)
        t.category_id, t.category_source = category.id, "user"
    db.flush()
    people = {sender_of(t) for t in txns}
    who = people.pop() if len(people) == 1 else None
    if category.kind == "transfer":
        if who and category.name == "Transfer":
            prefs = budget.preferences(db)
            budget.set_preferences(db, own_senders=sorted({*prefs.get("own_senders", []), who}))
            db.flush()
            transfers.recheck_own(db, budget.today() - timedelta(days=400))
        return 0
    if not remember:
        return 0
    return learn(db, txns[0], category, user_id, who)


def learn(db: DbSession, t: Transaction, category: Category, user_id: int | None, who: str | None = None) -> int:
    """Makes (or changes) the rule for t's merchant, or for the person a Zelle or Venmo payment went to, and sorts
    everything again. Returns how many transactions changed."""
    who = who or sender_of(t)
    pattern = PERSON + who if who else normalize_merchant(t.merchant)
    if not pattern:
        return 0
    rule = db.scalar(select(MerchantRule).where(MerchantRule.pattern == pattern))
    if rule is None:
        db.add(MerchantRule(pattern=pattern, category_id=category.id, created_from_txn_id=t.id, created_by=user_id))
    else:
        rule.category_id = category.id
    db.flush()
    if who:
        transfers.release_person(db, who)
    return categorize(db)
