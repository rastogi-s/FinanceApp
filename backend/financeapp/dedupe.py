"""One login, one connection. Linking the same bank login through Plaid twice (a reconnect done as a fresh link, or
adding it again) makes a second Item with new ids for the same accounts, and everything would count twice. This
finds such pairs and folds the newer one into the older one: the older accounts keep their history, categories,
goal money and settings, the newer one's records are matched up with them (or moved over when they're new), and
only one Item is kept syncing.

When the kept Item is the newer one, the older accounts take its account ids. Their transactions that it hasn't
sent yet are marked "was:<old id>", and the sync hands each one the new id when the same transaction arrives
(`claim`), so a full history coming in from the new Item never adds copies.
"""

import logging
from collections.abc import Callable, Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from financeapp import crypto
from financeapp.models import (
    Account,
    AuditEvent,
    Balance,
    Connection,
    GoalFunding,
    HoldingSnapshot,
    Import,
    InvestmentTransaction,
    Transaction,
)
from financeapp.transfers import brokerage_group, unmatch

log = logging.getLogger("financeapp")
ORPHAN = "was:"
CLAIM_DAYS = 3  # a pending charge can post a few days later, under the other Item's id


def _key(a: Account) -> tuple[str, str] | None:
    return (a.type, a.mask) if a.mask else None


def same_institution(a: Connection, b: Connection) -> bool:
    ia, ib = (a.state or {}).get("institution_id"), (b.state or {}).get("institution_id")
    if ia and ib:
        return ia == ib
    return a.institution.strip().lower() == b.institution.strip().lower()


def pairs_of(db: DbSession, keep: Connection, extra: Connection) -> list[tuple[Account, Account]] | None:
    """Each of `extra`'s accounts with the same account in `keep` (same type and last 4 digits), or None when they
    aren't the same login: any account without a twin (a second person's login at the same bank) means no merge."""
    mine = list(db.scalars(select(Account).where(Account.connection_id == keep.id)))
    theirs = list(db.scalars(select(Account).where(Account.connection_id == extra.id)))
    if not theirs:
        return None
    by_key: dict[tuple[str, str], list[Account]] = {}
    for a in mine:
        if (k := _key(a)) is not None:
            by_key.setdefault(k, []).append(a)
    out = []
    for b in theirs:
        k = _key(b)
        twins = by_key.get(k, []) if k else []
        if len(twins) != 1 or sum(1 for x in theirs if _key(x) == k) != 1:
            return None
        out.append((twins[0], b))
    return out


def matches_login(
    db: DbSession, institution: str, institution_id: str | None, accounts: list[dict]
) -> Connection | None:
    """An existing Plaid connection that is this login (from what Plaid Link says was just linked), if any."""
    keys = [(a.get("type"), a.get("mask")) for a in accounts]
    if not keys or any(not m for _t, m in keys) or len(set(keys)) != len(keys):
        return None
    probe = Connection(provider="plaid", institution=institution or "", state={"institution_id": institution_id})
    for c in db.scalars(
        select(Connection).where(Connection.provider == "plaid", Connection.status != "revoked").order_by(Connection.id)
    ):
        if not same_institution(c, probe):
            continue
        have = {_key(a) for a in db.scalars(select(Account).where(Account.connection_id == c.id))}
        if all(k in have for k in keys):
            return c
    return None


def healthy(c: Connection) -> bool:
    return bool(c.token_enc) and c.status == "active" and not c.failure_count


def merge_duplicates(db: DbSession) -> list[tuple[str, str]]:
    """Folds every duplicate Plaid connection into the older one. Returns the (access token, item id) of each Item
    no longer used, to remove at Plaid once this is committed."""
    dropped: list[tuple[str, str]] = []
    while True:
        live = list(
            db.scalars(
                select(Connection)
                .where(Connection.provider == "plaid", Connection.status != "revoked")
                .order_by(Connection.created_at, Connection.id)
            )
        )
        found = None
        for i, keep in enumerate(live):
            for extra in live[i + 1 :]:
                if same_institution(keep, extra) and (pairs := pairs_of(db, keep, extra)) is not None:
                    found = keep, extra, pairs
                    break
            if found:
                break
        if found is None:
            return dropped
        if (item := merge(db, *found)) is not None:
            dropped.append(item)


def merge(
    db: DbSession,
    keep: Connection,
    extra: Connection,
    pairs: list[tuple[Account, Account]],
) -> tuple[str, str] | None:
    # Keep syncing through whichever Item works: the newer one when the older needs signing in again (the reconnect
    # case), otherwise the older one, so nothing about it changes.
    use_extra = healthy(extra) and not healthy(keep)
    for a, b in pairs:
        merge_account(db, a, b, rekey=use_extra)
    dropped_token = keep.token_enc if use_extra else extra.token_enc
    dropped_item = keep.external_id if use_extra else extra.external_id
    if use_extra:
        keep.token_enc = extra.token_enc
        keep.external_id = extra.external_id
        keep.state = {**(extra.state or {}), "institution": keep.institution}
        keep.status = extra.status
        keep.failure_count = extra.failure_count
        keep.last_error = extra.last_error
        keep.last_synced_at = extra.last_synced_at
    elif (extra.state or {}).get("institution_id") and not (keep.state or {}).get("institution_id"):
        keep.state = {**(keep.state or {}), "institution_id": extra.state["institution_id"]}
    db.add(
        AuditEvent(
            action="connection_merged",
            detail={"institution": keep.institution, "accounts": len(pairs), "kept_newer_link": use_extra},
        )
    )
    log.info("merged duplicate %s connection %s into %s", keep.institution, extra.id, keep.id)
    db.delete(extra)
    db.flush()
    return (crypto.decrypt(dropped_token, "connection:plaid"), dropped_item or "") if dropped_token else None


def merge_account(db: DbSession, a: Account, b: Account, rekey: bool) -> None:
    """Moves everything `b` has onto `a`, which keeps its own settings. With `rekey`, `a` takes `b`'s ids."""
    _merge_transactions(db, a, b, rekey)
    _merge_investment_transactions(db, a, b, rekey)

    mine = {x.date: x for x in db.scalars(select(Balance).where(Balance.account_id == a.id))}
    for x in list(db.scalars(select(Balance).where(Balance.account_id == b.id))):
        if x.date in mine:
            if rekey:  # the same day from the Item that's kept
                mine[x.date].current_cents, mine[x.date].available_cents = x.current_cents, x.available_cents
            db.delete(x)
        else:
            x.account_id = a.id
    db.flush()

    days = set(db.scalars(select(HoldingSnapshot.date).where(HoldingSnapshot.account_id == a.id).distinct()))
    for day in set(db.scalars(select(HoldingSnapshot.date).where(HoldingSnapshot.account_id == b.id).distinct())):
        if day in days and not rekey:
            db.execute(delete(HoldingSnapshot).where(HoldingSnapshot.account_id == b.id, HoldingSnapshot.date == day))
            continue
        if day in days:
            db.execute(delete(HoldingSnapshot).where(HoldingSnapshot.account_id == a.id, HoldingSnapshot.date == day))
        for h in db.scalars(
            select(HoldingSnapshot).where(HoldingSnapshot.account_id == b.id, HoldingSnapshot.date == day)
        ):
            h.account_id = a.id
    db.flush()

    goals = set(db.scalars(select(GoalFunding.goal_id).where(GoalFunding.account_id == a.id)))
    for f in list(db.scalars(select(GoalFunding).where(GoalFunding.account_id == b.id))):
        if f.goal_id in goals:
            db.delete(f)
        else:
            f.account_id = a.id
    for i in db.scalars(select(Import).where(Import.account_id == b.id)):
        i.account_id = a.id
    db.flush()

    if rekey:
        a.external_id = b.external_id
        a.subtype = b.subtype or a.subtype
    db.delete(b)
    db.flush()


def _pair_up(mine: list, theirs: list, same: Callable, near: Callable) -> list[tuple]:
    """One-to-one: exact twins first, then the closest look-alike within a few days."""
    left = list(mine)
    out = []
    rest = []
    for t in theirs:
        hit = next((m for m in left if same(m, t)), None)
        if hit is None:
            rest.append(t)
            continue
        left.remove(hit)
        out.append((hit, t))
    for t in rest:
        options = [m for m in left if near(m, t)]
        if not options:
            out.append((None, t))
            continue
        hit = min(
            options,
            key=lambda m: (
                m.merchant != t.merchant if hasattr(m, "merchant") else m.name != t.name,
                abs((m.date - t.date).days),
                m.id,
            ),
        )
        left.remove(hit)
        out.append((hit, t))
    return out


def _txn_same(m: Transaction, t: Transaction) -> bool:
    return (m.date, m.amount_cents, m.merchant) == (t.date, t.amount_cents, t.merchant)


def _txn_near(m: Transaction, t: Transaction) -> bool:
    return m.amount_cents == t.amount_cents and abs((m.date - t.date).days) <= CLAIM_DAYS


def _merge_transactions(db: DbSession, a: Account, b: Account, rekey: bool) -> None:
    mine = list(db.scalars(select(Transaction).where(Transaction.account_id == a.id)))
    theirs = list(db.scalars(select(Transaction).where(Transaction.account_id == b.id)))
    matched: set[int] = set()
    for m, t in _pair_up(mine, theirs, _txn_same, _txn_near):
        if m is None:
            t.account_id = a.id
            continue
        matched.add(m.id)
        if t.category_source == "user" and m.category_source != "user":
            m.category_id, m.category_source = t.category_id, "user"
        unmatch(db, t)  # its transfer partner gets matched again, with the one that's kept
        new = (t.external_id, t.date, t.amount_cents, t.pending)
        db.delete(t)
        db.flush()
        if rekey:
            m.external_id, m.date, m.amount_cents, m.pending = new
    if rekey:
        for m in mine:
            if m.id not in matched and not m.external_id.startswith(ORPHAN):
                m.external_id = (ORPHAN + m.external_id)[:200]
    db.flush()


def _inv_same(m: InvestmentTransaction, t: InvestmentTransaction) -> bool:
    return (m.date, m.amount_cents, m.type, m.symbol, m.quantity) == (
        t.date,
        t.amount_cents,
        t.type,
        t.symbol,
        t.quantity,
    )


def _inv_near(m: InvestmentTransaction, t: InvestmentTransaction) -> bool:
    return (m.amount_cents, m.type, m.symbol) == (t.amount_cents, t.type, t.symbol) and abs(
        (m.date - t.date).days
    ) <= CLAIM_DAYS


def _release_brokerage_pair(db: DbSession, t: InvestmentTransaction) -> None:
    for bank in db.scalars(select(Transaction).where(Transaction.transfer_group_id == brokerage_group(t))):
        unmatch(db, bank)


def _merge_investment_transactions(db: DbSession, a: Account, b: Account, rekey: bool) -> None:
    mine = list(db.scalars(select(InvestmentTransaction).where(InvestmentTransaction.account_id == a.id)))
    theirs = list(db.scalars(select(InvestmentTransaction).where(InvestmentTransaction.account_id == b.id)))
    matched: set[int] = set()
    for m, t in _pair_up(mine, theirs, _inv_same, _inv_near):
        if m is None:
            t.account_id = a.id
            continue
        matched.add(m.id)
        _release_brokerage_pair(db, t)
        new_id = t.external_id
        db.delete(t)
        db.flush()
        if rekey:
            m.external_id = new_id
    if rekey:
        for m in mine:
            if m.id not in matched and not m.external_id.startswith(ORPHAN):
                m.external_id = (ORPHAN + m.external_id)[:200]
    db.flush()


def claim(orphans: list, data, same: Callable) -> object | None:
    """The orphan (see the module notes) that `data` is, if any; it's taken off the list."""
    options = [o for o in orphans if same(o, data) and abs((o.date - data.date).days) <= CLAIM_DAYS]
    if not options:
        return None
    hit = min(
        options,
        key=lambda o: (
            getattr(o, "merchant", None) != getattr(data, "merchant", None),
            abs((o.date - data.date).days),
            o.id,
        ),
    )
    orphans.remove(hit)
    return hit


def orphans_of(db: DbSession, model, account_ids: Iterable[int]) -> dict[int, list]:
    out: dict[int, list] = {}
    ids = list(account_ids)
    if not ids:
        return out
    for o in db.scalars(select(model).where(model.account_id.in_(ids), model.external_id.startswith(ORPHAN))):
        out.setdefault(o.account_id, []).append(o)
    return out
