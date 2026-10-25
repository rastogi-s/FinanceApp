"""Money in and out the wrong way round: turning uploaded transactions around after they were saved.

A file can come in backwards (a card export the app read as a bank one, a statement AI misread, or the switch at
upload set the wrong way). Turning a transaction around flips its sign, splits any transfer it was paired into
(purchases on a backwards card look like card payments), and lets categorizing and transfer matching look at it
again. A category picked by hand stays. Works for any uploaded or typed-in account.

Uploaded transactions are identified by date, signed amount and description (files.stable_id), so the id moves with
the sign. That way the next upload of the same rows, read the right way round, finds them instead of adding them
twice. A row whose new id is already in the account (the same file uploaded again the right way round) is a
duplicate and is removed. Uploading a file again the right way round also turns the backwards rows around by itself
(see claim_reversed).
"""

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp.categorize import categorize
from financeapp.connectors.base import TransactionData
from financeapp.connectors.files import stable_id
from financeapp.models import Account, Import, Transaction
from financeapp.transfers import match_transfers, unmatch

# How many identical rows on one day (two coffees) to look through when working out an id.
MAX_SAME = 50


def _ordinal(external_id: str, on: date, cents: int, description: str) -> int | None:
    """Which of the identical rows this id is (1, 2...), when it's a file id made from these values."""
    if not external_id.startswith("file-"):
        return None
    key = description.lower()
    for n in range(1, MAX_SAME + 1):
        if stable_id(on.isoformat(), cents, key, n) == external_id:
            return n
    return None


def id_for_sign(external_id: str, on: date, cents: int, description: str) -> str:
    """The id a row gets with `cents` as its amount. An id made from the opposite sign moves over; any other id (an
    OFX FITID, a typed-in row) never depended on the sign and stays."""
    n = _ordinal(external_id, on, -cents, description) or _ordinal(external_id, on, cents, description)
    return stable_id(on.isoformat(), cents, description.lower(), n) if n else external_id


def resign(data: TransactionData) -> None:
    """Gives a parsed row whose amount was just turned around the id that goes with its new sign."""
    data.external_id = id_for_sign(data.external_id, data.date, data.amount_cents, data.description)


def opposite_id(data: TransactionData) -> str | None:
    """The id this parsed row would have had with the opposite sign, or None for ids that don't depend on it."""
    n = _ordinal(data.external_id, data.date, data.amount_cents, data.description)
    return stable_id(data.date.isoformat(), -data.amount_cents, data.description.lower(), n) if n else None


def reversed_matches(have: dict[str, Transaction], txns: list[TransactionData]) -> dict[str, Transaction]:
    """Rows of an upload that are already in the account under the opposite sign's id: {upload id: saved row}."""
    out: dict[str, Transaction] = {}
    for data in txns:
        if data.external_id in have:
            continue
        other = opposite_id(data)
        if other and other in have and have[other] not in out.values():
            out[data.external_id] = have[other]
    return out


def claim_reversed(db: DbSession, account: Account, txns: list[TransactionData]) -> int:
    """Before an upload is saved: rows already here the other way round are turned around (or, saved by an older
    version with the wrong id for their sign, just given the right id), so they're updated, not added again."""
    have = {t.external_id: t for t in db.scalars(select(Transaction).where(Transaction.account_id == account.id))}
    matches = reversed_matches(have, txns)
    amounts = {d.external_id: d.amount_cents for d in txns}
    backwards = [t for uid, t in matches.items() if t.amount_cents == -amounts[uid]]
    for uid, t in matches.items():
        if t not in backwards:
            t.external_id = uid
    db.flush()
    turn_around(db, backwards, rematch=False)
    return len(backwards)


def turn_around(db: DbSession, txns: list[Transaction], rematch: bool = True) -> dict:
    """Flips the sign of these transactions (all from one account) and fixes what was worked out from it."""
    if not txns:
        return {"flipped": 0, "removed": 0}
    mine = {t.id for t in txns}
    others = set(
        db.scalars(
            select(Transaction.external_id).where(
                Transaction.account_id == txns[0].account_id, Transaction.id.notin_(mine)
            )
        )
    )
    for t in txns:
        unmatch(db, t)  # the other side of a pair goes back to normal categorizing too
    removed = 0
    keep: list[tuple[Transaction, str]] = []
    for t in txns:
        t.amount_cents = -t.amount_cents
        new_id = id_for_sign(t.external_id, t.date, t.amount_cents, t.description)
        if new_id in others:
            db.delete(t)  # the same row is already here the right way round
            removed += 1
            continue
        if t.category_source != "user":
            t.category_id, t.category_source = None, "none"
        t.transfer_group_id = None
        keep.append((t, new_id))
    # Two steps, so ids can swap places (a purchase and its refund on one day) without tripping the unique key.
    for t, _ in keep:
        t.external_id = f"flip-{t.id}"
    db.flush()
    for t, new_id in keep:
        t.external_id = new_id
    db.flush()
    if rematch:
        match_transfers(db, min(t.date for t, _ in keep) if keep else None)
        categorize(db)
    return {"flipped": len(keep), "removed": removed}


def uploads_of(db: DbSession, account: Account) -> dict:
    """What turning an account around would touch: its transactions, money in and out, and each upload into it."""
    rows = db.execute(
        select(Transaction.import_id, Transaction.amount_cents, Transaction.date).where(
            Transaction.account_id == account.id
        )
    ).all()
    by_import: dict[int | None, list] = defaultdict(list)
    for import_id, cents, on in rows:
        by_import[import_id].append((cents, on))
    names = {
        i.id: (i.filename, i.created_at)
        for i in db.scalars(select(Import).where(Import.id.in_([k for k in by_import if k is not None])))
    }

    def totals(items: list) -> dict:
        dates = sorted(on for _, on in items)
        return {
            "transactions": len(items),
            "spent_cents": -sum(c for c, _ in items if c < 0),
            "received_cents": sum(c for c, _ in items if c > 0),
            "first_date": dates[0].isoformat() if dates else None,
            "last_date": dates[-1].isoformat() if dates else None,
        }

    uploads = []
    for import_id, items in by_import.items():
        filename, created = names.get(import_id, (None, None)) if import_id is not None else (None, None)
        uploads.append(
            {
                "import_id": import_id,
                "filename": filename or ("Typed in or older uploads" if import_id is None else "Upload"),
                "uploaded_at": created.isoformat() if created else None,
                **totals(items),
            }
        )
    uploads.sort(key=lambda u: u["uploaded_at"] or "", reverse=True)
    everything = [(cents, on) for _, cents, on in rows]
    return {"account_id": account.id, "type": account.type, **totals(everything), "uploads": uploads}
