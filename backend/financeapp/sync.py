"""The sync: asks every connection for balances, holdings and new transactions, saves them, then finds transfers
and categorizes. Runs every few hours in the background (scheduler.py) and on demand from the app.

Each connection is saved in its own transaction, so one broken link never stops the others. A connection that
fails twice in a row (or needs the person to sign in at the bank again) is marked stale, shows a warning in the
app, and sends one alert. Tokens are decrypted only for the moment they're used, and error messages saved or
logged never contain them.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import crypto, dedupe, notify
from financeapp.categorize import categorize
from financeapp.config import Settings
from financeapp.connectors.base import (
    AccountData,
    Connector,
    ConnectorError,
    FetchResult,
    HoldingData,
    InvestmentTransactionData,
    TransactionData,
)
from financeapp.db import Database
from financeapp.models import (
    Account,
    AccountTerms,
    AuditEvent,
    Balance,
    CardStatement,
    Connection,
    HoldingSnapshot,
    InvestmentTransaction,
    SyncRun,
    Transaction,
    now,
)
from financeapp.transfers import match_transfers

log = logging.getLogger("financeapp")
SYNC_LOCK = 72_416_002
STALE_AFTER = 2
TRANSFER_DAYS = 400  # all the history the 6-month averages look at, so an older card payment isn't spending


def connector_for(provider: str, settings: Settings) -> Connector | None:
    if provider == "plaid" and settings.plaid_client_id and settings.plaid_secret:
        from financeapp.connectors.plaid import Plaid

        return Plaid(settings.plaid_client_id, settings.plaid_secret, settings.plaid_env)
    if provider == "snaptrade" and settings.snaptrade_client_id and settings.snaptrade_consumer_key:
        from financeapp.connectors.snaptrade import SnapTrade

        return SnapTrade(settings.snaptrade_client_id, settings.snaptrade_consumer_key)
    return None


def today() -> date:
    return datetime.now(UTC).astimezone().date()


# ---- saving what a connector returned (also used by file imports)


def save_accounts(db: DbSession, connection: Connection, accounts: list[AccountData], on: date) -> dict[str, Account]:
    existing = {a.external_id: a for a in db.scalars(select(Account).where(Account.connection_id == connection.id))}
    for data in accounts:
        a = existing.get(data.external_id)
        if a is None:
            a = Account(
                connection_id=connection.id,
                external_id=data.external_id,
                owner_user_id=connection.created_by,
                name=data.name[:120],
                institution=data.institution[:120],
                type=data.type,
            )
            db.add(a)
            existing[data.external_id] = a
        a.institution = (data.institution or a.institution)[:120]
        a.type = data.type
        a.subtype = data.subtype
        a.mask = data.mask
        if data.limit_cents is not None:
            a.credit_limit_cents = data.limit_cents
        db.flush()
        if data.current_cents is not None:
            save_balance(db, a.id, on, data.current_cents, data.available_cents)
        if data.apy is not None:  # a bank that sends none keeps the last one (deposits.py)
            terms = db.get(AccountTerms, a.id) or AccountTerms(account_id=a.id, rates={})
            terms.bank_apy = Decimal(str(round(data.apy, 3)))
            db.merge(terms)
    return existing


def save_balance(db: DbSession, account_id: int, on: date, current: int, available: int | None = None) -> None:
    stmt = insert(Balance).values(account_id=account_id, date=on, current_cents=current, available_cents=available)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["account_id", "date"],
            set_={"current_cents": stmt.excluded.current_cents, "available_cents": stmt.excluded.available_cents},
        )
    )


def save_holdings(db: DbSession, accounts: dict[str, Account], holdings: list[HoldingData], on: date) -> int:
    """Replaces the day's holdings for these accounts. Same symbol twice in one account (lots) is added up."""
    ids = [a.id for a in accounts.values()]
    db.execute(delete(HoldingSnapshot).where(HoldingSnapshot.date == on, HoldingSnapshot.account_id.in_(ids)))
    merged: dict[tuple[int, str], HoldingSnapshot] = {}
    for h in holdings:
        account = accounts.get(h.account_external_id)
        if account is None:
            continue
        key = (account.id, h.symbol)
        if key in merged:
            m = merged[key]
            m.quantity += h.quantity
            m.value_cents += h.value_cents
            if h.cost_basis_cents is not None:
                m.cost_basis_cents = (m.cost_basis_cents or 0) + h.cost_basis_cents
            if h.unvested_quantity:
                m.unvested_quantity = (m.unvested_quantity or 0) + h.unvested_quantity
            continue
        merged[key] = HoldingSnapshot(
            date=on,
            account_id=account.id,
            symbol=h.symbol,
            name=h.name[:200],
            asset_class=h.asset_class,
            quantity=h.quantity,
            price=h.price,
            value_cents=h.value_cents,
            cost_basis_cents=h.cost_basis_cents,
            unvested_quantity=h.unvested_quantity or None,
        )
    db.add_all(merged.values())
    db.flush()
    return len(merged)


def save_transactions(
    db: DbSession, accounts: dict[str, Account], txns: list[TransactionData], source: str, import_id: int | None = None
) -> list[Transaction]:
    """Adds new transactions and updates changed ones (keeping a category picked by hand). Returns the new ones."""
    by_account: dict[int, dict[str, Transaction]] = {}
    for account in accounts.values():
        by_account[account.id] = {
            t.external_id: t for t in db.scalars(select(Transaction).where(Transaction.account_id == account.id))
        }
    # After two links of one login were merged: history the other Item already brought in gets its new id.
    orphans = dedupe.orphans_of(db, Transaction, by_account)
    added = []
    for data in txns:
        account = accounts.get(data.account_external_id)
        if account is None:
            continue
        t = by_account[account.id].get(data.external_id)
        if t is None and orphans.get(account.id):
            t = dedupe.claim(orphans[account.id], data, lambda o, d: o.amount_cents == d.amount_cents)
            if t is not None:
                t.external_id = data.external_id
                by_account[account.id][data.external_id] = t
        if t is None:
            t = Transaction(
                account_id=account.id,
                external_id=data.external_id,
                source=source,
                import_id=import_id,
                category_source="none",
            )
            db.add(t)
            by_account[account.id][data.external_id] = t
            added.append(t)
        elif (t.amount_cents, t.date) != (data.amount_cents, data.date) and t.transfer_group_id is not None:
            # A pending transfer that posted with a different amount or day: let the matcher look at it again.
            from financeapp.transfers import unmatch

            unmatch(db, t)
        t.date = data.date
        t.amount_cents = data.amount_cents
        t.merchant = data.merchant
        t.description = data.description
        t.pending = data.pending
        t.provider_category = data.provider_category
        t.mcc, t.channel, t.website = data.mcc, data.channel, data.website
    db.flush()
    return added


def save_card_statements(db: DbSession, accounts: dict[str, Account], statements: list, on: date) -> None:
    for s in statements:
        account = accounts.get(s.account_external_id)
        if account is None:
            continue
        row = db.get(CardStatement, account.id) or CardStatement(account_id=account.id)
        row.as_of = on
        row.purchase_apr = s.purchase_apr
        row.aprs = s.aprs
        row.statement_cents, row.statement_date = s.statement_cents, s.statement_date
        row.minimum_cents, row.due_date = s.minimum_cents, s.due_date
        row.last_payment_cents, row.last_payment_date = s.last_payment_cents, s.last_payment_date
        row.overdue = s.overdue
        db.merge(row)


def save_investment_transactions(
    db: DbSession, accounts: dict[str, Account], txns: list[InvestmentTransactionData], source: str
) -> int:
    """Adds new brokerage activity and updates what changed. Returns how many were new."""
    ids = [a.id for a in accounts.values()]
    existing = {
        (t.account_id, t.external_id): t
        for t in db.scalars(select(InvestmentTransaction).where(InvestmentTransaction.account_id.in_(ids)))
    }
    orphans = dedupe.orphans_of(db, InvestmentTransaction, ids)
    added = 0
    for data in txns:
        account = accounts.get(data.account_external_id)
        if account is None:
            continue
        t = existing.get((account.id, data.external_id))
        if t is None and orphans.get(account.id):
            t = dedupe.claim(
                orphans[account.id],
                data,
                lambda o, d: (o.amount_cents, o.type, o.symbol) == (d.amount_cents, d.type, d.symbol),
            )
            if t is not None:
                t.external_id = data.external_id
                existing[(account.id, data.external_id)] = t
        if t is None:
            t = InvestmentTransaction(account_id=account.id, external_id=data.external_id, source=source)
            db.add(t)
            existing[(account.id, data.external_id)] = t
            added += 1
        t.date = data.date
        t.type = data.type
        t.subtype = data.subtype
        t.symbol = data.symbol
        t.name = data.name
        t.quantity = data.quantity
        t.price = data.price
        t.amount_cents = data.amount_cents
    db.flush()
    return added


def save_fetch(db: DbSession, connection: Connection, result: FetchResult, on: date) -> dict:
    accounts = save_accounts(db, connection, result.accounts, on)
    holdings = save_holdings(db, accounts, result.holdings, on) if result.holdings is not None else 0
    # Investment accounts without a balance from the provider: the holdings' total is the balance.
    if result.holdings is not None:
        for a in accounts.values():
            if a.type == "investment" and db.get(Balance, (a.id, on)) is None:
                total = sum(h.value_cents for h in result.holdings if accounts.get(h.account_external_id) is a)
                save_balance(db, a.id, on, total)
    added = save_transactions(db, accounts, result.transactions, connection.provider)
    activity = save_investment_transactions(db, accounts, result.investment_transactions, connection.provider)
    save_card_statements(db, accounts, result.card_statements, on)
    removed = 0
    if result.removed_transaction_ids:
        ids = [a.id for a in accounts.values()]
        removed = db.execute(
            delete(Transaction).where(
                Transaction.account_id.in_(ids), Transaction.external_id.in_(result.removed_transaction_ids)
            )
        ).rowcount
    connection.state = result.state
    return {
        "accounts": len(accounts),
        "holdings": holdings,
        "new_transactions": len(added),
        "removed": removed,
        "new_investment_activity": activity,
    }


# ---- the sync itself


def sync_connection(db: DbSession, connection: Connection, settings: Settings, on: date) -> dict:
    connector = connector_for(connection.provider, settings)
    if connector is None:
        raise ConnectorError(f"{connection.provider} isn't set up on the server (see .env)", "NOT_CONFIGURED")
    if not connection.token_enc:
        raise ConnectorError("This connection has no credentials (it was revoked).", "REVOKED", needs_relink=True)
    token = crypto.decrypt(connection.token_enc, f"connection:{connection.provider}")
    result = connector.fetch(token, connection.external_id or "", connection.state or {})
    return save_fetch(db, connection, result, on)


def run(database: Database, settings: Settings, trigger: str, only: list[int] | None = None) -> SyncRun | None:
    """Syncs every active connection (or just `only`). Returns None if another sync is already running."""
    with database.engine.connect() as lock_conn:
        if not lock_conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": SYNC_LOCK}).scalar():
            return None
        try:
            return _run(database, settings, trigger, only)
        finally:
            lock_conn.execute(text("select pg_advisory_unlock(:k)"), {"k": SYNC_LOCK})
            lock_conn.commit()


def _run(database: Database, settings: Settings, trigger: str, only: list[int] | None) -> SyncRun:
    on = today()
    with database.session() as db:
        run_ = SyncRun(trigger=trigger, summary={})
        db.add(run_)
        db.flush()
        run_id = run_.id
        q = select(Connection.id).where(Connection.provider.in_(["plaid", "snaptrade"]), Connection.status != "revoked")
        if only:
            q = q.where(Connection.id.in_(only))
        ids = list(db.scalars(q))

    results: dict[str, dict] = {}
    all_ok = True
    for cid in ids:
        with database.session() as db:
            c = db.get_one(Connection, cid)
            try:
                results[str(cid)] = {"ok": True, **sync_connection(db, c, settings, on)}
                c.failure_count = 0
                c.last_error = None
                c.status = "active"
                c.last_synced_at = now()
            except Exception as e:  # one connection failing never stops the others
                db.rollback()
                c = db.get_one(Connection, cid)
                all_ok = False
                code = e.code if isinstance(e, ConnectorError) else type(e).__name__
                message = str(e) if isinstance(e, ConnectorError) else "Unexpected error while syncing"
                log.warning("sync of connection %s (%s) failed: %s", cid, c.provider, code)
                results[str(cid)] = {"ok": False, "code": code}
                c.failure_count += 1
                c.last_error = message[:300]
                relink = isinstance(e, ConnectorError) and e.needs_relink
                if c.status != "stale" and (relink or c.failure_count >= STALE_AFTER):
                    c.status = "stale"
                    db.add(AuditEvent(action="connection_stale", detail={"institution": c.institution, "code": code}))
                    notify.send(
                        settings,
                        "A connection stopped working",
                        f"{c.institution} couldn't sync. Open Settings → Accounts to reconnect it, or upload "
                        "a file from the bank instead.",
                        tags="warning",
                    )

    dropped: list[tuple[str, str]] = []
    try:
        with database.session() as db:
            merged = dedupe.merge_duplicates(db)
        dropped = merged
    except Exception:  # never fails the sync; the duplicate stays until the next one
        log.exception("merging duplicate connections failed")
    for token, item in dropped:  # only once the merge is saved: the Item no longer used stops syncing at Plaid
        try:
            connector = connector_for("plaid", settings)
            if connector is not None:
                connector.revoke(token, item)
        except Exception:
            log.warning("couldn't remove a duplicate Item at Plaid")

    with database.session() as db:
        pairs = match_transfers(db, on - timedelta(days=TRANSFER_DAYS))
        changed = categorize(db)
        r = db.get_one(SyncRun, run_id)
        r.finished_at = now()
        r.ok = all_ok
        r.summary = {"connections": results, "transfer_pairs": pairs, "categorized": changed}
        db.add(AuditEvent(action="sync", detail={"trigger": trigger, "ok": all_ok, "connections": len(ids)}))
    try:
        from financeapp import alerts

        alerts.check(database, settings, {"budget", "concentration", "goal"})
    except Exception:  # an alert problem never fails the sync
        log.exception("checking alerts after the sync failed")
    try:
        from financeapp import budget

        with database.session() as db:
            budget.ask_for_stubs(db, settings)
    except Exception:
        log.exception("asking for pay stubs after the sync failed")
    return r
