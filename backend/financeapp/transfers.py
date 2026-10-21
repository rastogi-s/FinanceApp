"""Finds money moving between your own accounts, so it isn't counted as spending or income.

A card payment, a move to savings or a brokerage contribution shows up twice: money out of one account and the
same amount into another. Pairs of equal and opposite amounts in two different accounts within 5 days are tagged
as one transfer (they share a transfer_group_id) and put in the Transfer category.

Some transfers only show one side, because the other account isn't linked (paying a card you haven't connected,
or a payment arriving on a card whose bank account isn't connected). Those are recognised by their description
("AUTOPAY", "PAYMENT THANK YOU", ...) and also go in Transfer, without a pair.

A category someone picked by hand always wins: those transactions are never matched.
"""

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from financeapp.models import Account, Category, Transaction

WINDOW_DAYS = 5

# Paying a card from a bank account: the issuer's name next to a payment word; and the card's side of it.
CARD_PAYMENT = (
    r"(amex|american express|chase (credit|card)|citi|discover|capital one|barclay|synchrony|apple card|"
    r"bank of america|bofa|wells fargo card|us bank card|credit (card|crd)).{0,24}"
    r"(autopay|auto pay|e-?payment|epay|payment|pmt)|"
    r"payment thank you|payment received|autopay payment"
)
BROKERAGES = r"fidelity|e\*?trade|vanguard|schwab|robinhood|wealthfront|betterment"
ONE_SIDED = re.compile(
    CARD_PAYMENT + "|"
    # Moves between accounts, and into brokerages.
    r"online transfer|internet transfer|transfer (to|from)|xfer|funds transfer|"
    rf"({BROKERAGES}).{{0,24}}(transfer|contribution|moneyline|ach|deposit|invest)",
    re.IGNORECASE,
)

PAIRS_SQL = text(
    """
    select o.id as out_id, i.id as in_id, abs(o.date - i.date) as gap
    from transactions o
    join accounts oa on oa.id = o.account_id
    join transactions i on i.amount_cents = -o.amount_cents and i.account_id <> o.account_id
         and i.date between o.date - :window and o.date + :window
    join accounts ia on ia.id = i.account_id
    where o.amount_cents <= -100
      and o.date >= :since
      and o.transfer_group_id is null and i.transfer_group_id is null
      and not o.pending and not i.pending
      and o.category_source <> 'user' and i.category_source <> 'user'
      and oa.is_authoritative and ia.is_authoritative
    order by o.date, gap, i.id
    """
)


def match_transfers(db: DbSession, since: date | None = None) -> int:
    """Tags transfers among transactions on or after `since` (default: the last 90 days). Returns pairs found."""
    since = since or date.today() - timedelta(days=90)
    transfer = db.query(Category).filter_by(name="Transfer").one()
    used: set[int] = set()
    pairs = 0
    for out_id, in_id, _gap in db.execute(PAIRS_SQL, {"window": WINDOW_DAYS, "since": since}).all():
        if out_id in used or in_id in used:
            continue
        used.update((out_id, in_id))
        group = uuid.uuid4()
        for tid in (out_id, in_id):
            t = db.get_one(Transaction, tid)
            t.transfer_group_id = group
            t.category_id = transfer.id
            t.category_source = "transfer"
        pairs += 1

    # One-sided transfers: a payment arriving on a card is always the card being paid; a recognisable description
    # on anything else.
    singles = (
        db.query(Transaction, Account.type)
        .join(Account, Account.id == Transaction.account_id)
        .filter(
            Transaction.date >= since,
            Transaction.transfer_group_id.is_(None),
            Transaction.category_source.notin_(["user", "transfer"]),
        )
        .all()
    )
    for t, account_type in singles:
        card_payment = account_type in ("credit", "loan") and t.amount_cents > 0 and not _looks_like_refund(t)
        if card_payment or ONE_SIDED.search(f"{t.merchant} {t.description}"):
            t.category_id = transfer.id
            t.category_source = "transfer"
    db.flush()
    return pairs


def _looks_like_refund(t: Transaction) -> bool:
    return bool(re.search(r"refund|return|credit adj|reversal|cashback|cash back|reward", t.description, re.I))


def unmatch(db: DbSession, t: Transaction) -> None:
    """Splits a transfer pair (when someone recategorizes one side): both sides go back to normal categorizing."""
    if t.transfer_group_id is None:
        return
    for other in db.query(Transaction).filter(Transaction.transfer_group_id == t.transfer_group_id):
        other.transfer_group_id = None
        if other.category_source == "transfer":
            other.category_source = "none"
