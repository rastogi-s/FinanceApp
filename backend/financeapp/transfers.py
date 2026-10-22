"""Finds money moving between your own accounts, so it isn't counted as spending or income.

A card payment, a move to savings or a brokerage contribution shows up twice: money out of one account and the
same amount into another. Pairs of equal and opposite amounts in two different accounts within 5 days are tagged
as one transfer (they share a transfer_group_id) and put in the Transfer category, or in "Credit card payment" when
one side is a card.

Some transfers only show one side, because the other account isn't linked (paying a card you haven't connected,
or a payment arriving on a card whose bank account isn't connected). Those are recognised by their description
("AUTOPAY", "PAYMENT THANK YOU", ...) and also go in Transfer (or Credit card payment), without a pair.

A Zelle, Venmo or Cash App payment to or from the household's own name is a transfer too, even with the other
side not linked: the sender is read from the description and compared with the household's names and the senders
someone marked as theirs (budget preference "own_senders").

A category someone picked by hand always wins: those transactions are never matched.
"""

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from financeapp.categorize import categorize, normalize_merchant
from financeapp.models import Account, Category, Preference, Transaction, User

WINDOW_DAYS = 5

# Paying a card from a bank account: the issuer's name next to a payment word; and the card's side of it.
CARD_PAYMENT = (
    r"(amex|american express|chase (credit|card)|citi|discover|capital one|barclay|synchrony|\bsyf\b|apple card|"
    r"bank of america|bofa|wells fargo card|us bank card|credit (card|crd)|card ?member serv|card services).{0,24}"
    r"(autopay|auto pay|e-?payment|epay|payment|paymnt|pymt|pmt)|"
    r"payment thank you|payment received|autopay payment"
)
CARD_TEXT = re.compile(CARD_PAYMENT, re.IGNORECASE)
CARD_CODE = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"  # Plaid's own name for paying a card
CARD_CATEGORY = "Credit card payment"
# E*TRADE is Morgan Stanley: its transfers often say only that ("MORGAN STANLEY", "MSPBNA").
BROKERAGES = r"fidelity|e\*?trade|morgan stanley|mspbna|ms&co|vanguard|schwab|robinhood|wealthfront|betterment"
ONE_SIDED = re.compile(
    CARD_PAYMENT + "|"
    # Moves between accounts, and into brokerages.
    r"online transfer|internet transfer|transfer (to|from)|xfer|funds transfer|"
    rf"({BROKERAGES}).{{0,24}}(transfer|contribution|moneyline|ach|deposit|invest)",
    re.IGNORECASE,
)

P2P = re.compile(r"zelle|venmo|cash ?app|paypal|apple cash", re.IGNORECASE)
SENDER = re.compile(
    r"\b(?:from|to)\s+([a-z][a-z .'-]*?)(?=\s+(?:conf|confirmation|on|ref|id|for|memo|via|trn|txn)\b|\s*\d|$)"
)


def sender_of(t: Transaction) -> str | None:
    """Who sent (or got) a Zelle or Venmo payment, from its description: "Zelle payment from JANE DOE conf# 12"."""
    text = normalize_merchant(f"{t.merchant} {t.description}")
    if not P2P.search(text):
        return None
    m = SENDER.search(text)
    name = m.group(1).strip() if m else ""
    name = re.sub(r"^(zelle|venmo|cash app|cashapp|paypal)\s+", "", name)
    return name or None


def own_names(db: DbSession) -> set[str]:
    """The household's names (full names only: a first name alone is too common) and senders marked as theirs."""
    names = {normalize_merchant(u.name) for u in db.query(User) if len(u.name.split()) >= 2}
    p = db.get(Preference, "household")
    names |= set((p.value if p else {}).get("own_senders", []))
    return {n for n in names if n}


CASH_OUT = re.compile(r"cash ?out|instant transfer|standard transfer|transfer to bank|add to balance", re.IGNORECASE)


def is_own(t: Transaction, names: set[str]) -> bool:
    """A payment app moving the household's own money: a cash-out to the bank, or to or from their own name."""
    text = f"{t.merchant} {t.description}"
    if P2P.search(text) and CASH_OUT.search(text):
        return True
    sender = sender_of(t)
    return sender is not None and any(sender == n or sender.startswith(n + " ") for n in names)


PAIRS_SQL = text(
    """
    select o.id as out_id, i.id as in_id, abs(o.date - i.date) as gap, oa.type as out_type, ia.type as in_type
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
    for out_id, in_id, _gap, _out_type, _in_type in db.execute(
        PAIRS_SQL, {"window": WINDOW_DAYS, "since": since}
    ).all():
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
    names = own_names(db)
    for t, account_type in singles:
        paid_off = account_type in ("credit", "loan") and t.amount_cents > 0 and not _looks_like_refund(t)
        text = f"{t.merchant} {t.description}"
        if paid_off or ONE_SIDED.search(text) or t.provider_category == CARD_CODE or is_own(t, names):
            t.category_id = transfer.id
            t.category_source = "transfer"
    db.flush()
    label_card_payments(db, since)
    return pairs


def _is_card_payment(t: Transaction, account_type: str) -> bool:
    if account_type == "loan":
        return False  # a mortgage or car loan being paid is a plain transfer
    if account_type == "credit" and t.amount_cents > 0:
        return True
    return t.provider_category == CARD_CODE or bool(CARD_TEXT.search(f"{t.merchant} {t.description}"))


def label_card_payments(db: DbSession, since: date) -> None:
    """Puts each transfer that pays a card (either side of the pair is a card, or the description or Plaid says so)
    in Credit card payment, and every other transfer in Transfer. Runs over the history each time, so card
    payments sorted before this category existed move into it too. Categories picked by hand are left alone."""
    card = db.query(Category).filter_by(name=CARD_CATEGORY).one_or_none()
    transfer = db.query(Category).filter_by(name="Transfer").one()
    if card is None:
        return
    rows = (
        db.query(Transaction, Account.type)
        .join(Account, Account.id == Transaction.account_id)
        .filter(Transaction.date >= since - timedelta(days=WINDOW_DAYS), Transaction.category_source == "transfer")
        .all()
    )
    paying: set[uuid.UUID] = set()
    for t, account_type in rows:
        if t.transfer_group_id is not None and (account_type == "credit" or _is_card_payment(t, account_type)):
            paying.add(t.transfer_group_id)
    for t, account_type in rows:
        is_card = t.transfer_group_id in paying if t.transfer_group_id else _is_card_payment(t, account_type)
        want = card.id if is_card else transfer.id
        if t.category_id != want:
            t.category_id = want
    db.flush()


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


def recheck_own(db: DbSession, since: date) -> None:
    """After someone marks a sender as theirs or not: payments no longer from their own name go back to normal
    categorizing, and new ones become transfers."""
    names = own_names(db)
    back = []
    for t in db.query(Transaction).filter(
        Transaction.date >= since, Transaction.category_source == "transfer", Transaction.transfer_group_id.is_(None)
    ):
        if sender_of(t) is not None and not is_own(t, names) and not ONE_SIDED.search(f"{t.merchant} {t.description}"):
            t.category_id = None
            t.category_source = "none"
            back.append(t)
    db.flush()
    match_transfers(db, since)
    categorize(db, [t for t in back if t.category_source == "none"])
