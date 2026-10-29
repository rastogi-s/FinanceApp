"""The household's numbers, built from the database for the app's pages (the same shape demo.py makes up).

Net worth = cash + investments - card and loan balances, using each account's latest balance, and only accounts
marked authoritative (so an account that comes in through two sources is counted once). Transfers between your own
accounts are left out of spending. The budget, savings rate and "safe to spend" come from budget.py; the projection
comes from plan.py, and is null until there's a savings number to project (the Plan page says so).
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import alerts, bills, budget, deposits, plan, portfolio, quotes, reminders, review
from financeapp.models import (
    Account,
    AlertEvent,
    Balance,
    Category,
    Connection,
    HoldingSnapshot,
    SyncRun,
    Transaction,
    now,
)
from financeapp.sync import today

HISTORY_DAYS = 90
TRANSACTION_DAYS = 62  # always reaches back to the start of last month
ALERT_DAYS = 7  # unread alerts on Home
ASSET = {"depository", "investment", "other"}
DEBT = {"credit", "loan"}


def display_name(a: Account) -> str:
    name = a.name if a.institution.lower() in a.name.lower() else f"{a.institution} {a.name}"
    return f"{name} ••{a.mask}" if a.mask else name


def signed(a: Account, cents: int) -> int:
    return -cents if a.type in DEBT else cents


def net_worth_history(
    db: DbSession, accounts: list[Account], end: date, days: int = HISTORY_DAYS, close: dict[int, int] | None = None
) -> list[dict]:
    """Net worth each day. An account counts from the start at its first balance, so linking one isn't a jump (the
    same as the net worth sheet's windows). `close` (from last_close) replaces yesterday's balance of an account."""
    start = end - timedelta(days=days - 1)
    ids = [a.id for a in accounts]
    if not ids:
        return []
    rows = db.execute(
        select(Balance.account_id, Balance.date, Balance.current_cents)
        .where(Balance.account_id.in_(ids), Balance.date <= end)
        .order_by(Balance.date)
    ).all()
    by_account: dict[int, list[tuple[date, int]]] = defaultdict(list)
    for account_id, d, c in rows:
        by_account[account_id].append((d, c))
    for aid, cents in (close or {}).items():
        by_account[aid] = with_balance(by_account[aid], end - timedelta(days=1), cents)
    kinds = {a.id: a for a in accounts}
    first_day = min((v[0][0] for v in by_account.values()), default=end)
    history = []
    # Each day: every account's most recent balance on or before that day (accounts carry forward).
    pointers = {aid: -1 for aid in by_account}
    latest: dict[int, int] = {}
    day = min(start, first_day)
    while day <= end:
        for aid, series in by_account.items():
            i = pointers[aid]
            while i + 1 < len(series) and series[i + 1][0] <= day:
                i += 1
            pointers[aid] = i
            latest[aid] = series[max(i, 0)][1]
        if day >= max(start, first_day):
            history.append({"date": day.isoformat(), "cents": sum(signed(kinds[a], c) for a, c in latest.items())})
        day += timedelta(days=1)
    return history


def with_balance(series: list[tuple[date, int]], day: date, cents: int) -> list[tuple[date, int]]:
    """A balance series (oldest first) with the day's balance set to cents."""
    return sorted([(d, c) for d, c in series if d != day] + [(day, cents)])


def last_close(
    db: DbSession, accounts: list[Account], positions: list[portfolio.Position], end: date
) -> dict[int, tuple[int, dict[str, tuple[Decimal, Decimal, str]]]]:
    """Each investment account at yesterday's close, for "today": its last balance before today, with the holdings
    that have a live price today revalued at the close before today (the quote's previous close, or its price when
    the market hasn't traded today). The brokerage's own price can be a day behind, and without this, today's change
    would also hold yesterday's market move. Returns the balance and the revalued holdings, by account."""
    live = {(p.account_id, p.symbol) for p in positions if p.price_as_of is not None}
    found = quotes.latest(db, {s for _, s in live})
    day = {p.account_id: p.as_of for p in positions}
    latest = latest_balances(db, [a for a in accounts if a.id in day])
    out = {}
    for a in accounts:
        # Only where today is at live prices too (live_moves): the balance is from the day of the holdings.
        b = latest.get(a.id)
        if a.type != "investment" or b is None or b.date != day[a.id] or not any(i == a.id for i, _ in live):
            continue
        before = db.scalar(
            select(Balance).where(Balance.account_id == a.id, Balance.date < end).order_by(Balance.date.desc()).limit(1)
        )
        if before is None:
            continue
        rows = db.scalars(
            select(HoldingSnapshot).where(HoldingSnapshot.account_id == a.id, HoldingSnapshot.date == before.date)
        ).all()
        moved, holdings = 0, {}
        for h in rows:
            price = h.price
            q = found.get(h.symbol)
            quoted = q.as_of.astimezone().date() if q is not None else None
            if (a.id, h.symbol) in live and quoted is not None and quoted >= before.date and h.quantity > 0:
                close = q.previous_close if quoted >= end else q.price
                if close:
                    price = close
                    moved += int((h.quantity * close * 100).to_integral_value()) - h.value_cents
            holdings[h.symbol] = (h.quantity, price, h.name)
        if rows:
            out[a.id] = (before.current_cents + moved, holdings)
    return out


def counted(db: DbSession) -> list[Account]:
    """The accounts that count: not hidden, and the authoritative source of each real account."""
    return list(db.scalars(select(Account).where(Account.hidden.is_(False), Account.is_authoritative.is_(True))))


def latest_balances(db: DbSession, accounts: list[Account]) -> dict[int, Balance | None]:
    return {
        a.id: db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        for a in accounts
    }


def live_moves(latest: dict[int, Balance | None], positions: list[portfolio.Position]) -> dict[int, int]:
    """How far live prices move each investment account's balance. Only when the balance is from the same day as the
    holdings they revalue: an older balance doesn't know about the holdings' newer prices."""
    day = {p.account_id: p.as_of for p in positions}
    return {
        aid: c
        for aid, c in portfolio.live_change_by_account(positions).items()
        if c and latest.get(aid) is not None and latest[aid].date == day[aid]
    }


def build(db: DbSession) -> dict:
    return build_with_plan(db)[0]


def build_with_plan(db: DbSession) -> tuple[dict, dict | None]:
    """The household, and what its projection was made from (plan.inputs), for the Plan page."""
    accounts = counted(db)
    if not accounts:
        return {"demo": False, "linked": False}, None
    end = today()
    latest_balance = latest_balances(db, accounts)
    positions = portfolio.positions(db, accounts)
    close = {aid: cents for aid, (cents, _) in last_close(db, accounts, positions, end).items()}
    history = net_worth_history(db, accounts, end, close=close)
    net = history[-1]["cents"] if history else 0

    def account_row(a: Account) -> dict:
        b = latest_balance[a.id]
        return {
            "id": a.id,
            "name": display_name(a),
            "type": a.type,
            "cents": b.current_cents if b else 0,
            "as_of": b.date.isoformat() if b else None,
        }

    cash = [account_row(a) for a in accounts if a.type in ("depository", "other")]
    investments = [account_row(a) for a in accounts if a.type == "investment"]
    debts = [account_row(a) for a in accounts if a.type in DEBT]

    # Holdings: each account's most recent snapshot, at live prices when there are newer ones (portfolio.py).
    names = {a.id: display_name(a) for a in accounts}
    holdings = [
        {
            "symbol": p.symbol,
            "name": p.name,
            "asset_class": p.asset_class,
            "account": names[p.account_id],
            "account_id": p.account_id,
            "quantity": float(p.quantity),
            "price": float(p.price),
            "value_cents": p.value_cents,
            "change_today_percent": p.change_today_percent,
            "cost_basis_cents": p.cost_basis_cents,
            "as_of": p.as_of.isoformat(),
            "live": p.price_as_of is not None,
        }
        for p in positions
    ]
    moved = live_moves(latest_balance, positions)
    for row in investments:
        if moved.get(row["id"]):
            row["cents"] += moved[row["id"]]
            net += moved[row["id"]]
            if history:
                history[-1] = {**history[-1], "cents": history[-1]["cents"] + moved[row["id"]]}

    allocation: dict[str, int] = defaultdict(int)
    holdings_by_account: dict[str, int] = defaultdict(int)
    for h in holdings:
        allocation[h["asset_class"]] += h["value_cents"]
        holdings_by_account[h["account"]] += h["value_cents"]
    for row in investments:  # investment accounts with a balance but no holdings detail
        if holdings_by_account.get(row["name"], 0) == 0 and row["cents"]:
            allocation["Other investments"] += row["cents"]
    allocation["Cash"] += sum(r["cents"] for r in cash)

    yesterday = history[-2]["cents"] if len(history) > 1 else net

    # Transactions and spending.
    categories = {c.id: c for c in db.scalars(select(Category))}
    since = end - timedelta(days=TRANSACTION_DAYS)
    txns = db.scalars(
        select(Transaction)
        .where(Transaction.account_id.in_([a.id for a in accounts]), Transaction.date >= since)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    ).all()
    month_start = end.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    last_month_same_day = min(last_month_start + timedelta(days=end.day - 1), month_start - timedelta(days=1))

    def is_spending(t: Transaction) -> bool:
        c = categories.get(t.category_id) if t.category_id else None
        return t.transfer_group_id is None and (c is None or c.kind not in ("transfer", "income"))

    spent_month = -sum(t.amount_cents for t in txns if t.date >= month_start and is_spending(t))
    spent_last = -sum(
        t.amount_cents for t in txns if last_month_start <= t.date <= last_month_same_day and is_spending(t)
    )
    to_check = review.items(db)  # Check categories (review.py): everything worth a look, not only uncategorized

    transactions = []
    for t in txns:
        c = categories.get(t.category_id) if t.category_id else None
        transactions.append(
            {
                "id": str(t.id),
                "date": t.date.isoformat(),
                "merchant": t.merchant,
                "category": c.name if c else "Uncategorized",
                "category_id": t.category_id,
                "amount_cents": t.amount_cents,
                "account": names[t.account_id],
                "transfer": bool(t.transfer_group_id) or (c is not None and c.kind == "transfer"),
                "pending": t.pending,
                "needs_review": t.category_source == "none" and is_spending(t),
                "by_hand": t.category_source == "user",
            }
        )

    connections = db.scalars(select(Connection).where(Connection.status != "revoked")).all()
    last_run = db.scalar(select(SyncRun).where(SyncRun.finished_at.is_not(None)).order_by(SyncRun.started_at.desc()))
    synced_times = [c.last_synced_at for c in connections if c.last_synced_at]
    as_of = max(synced_times) if synced_times else datetime.now(UTC)

    budget_state = budget.build(db)
    allocation_rows = [
        {"asset_class": k, "cents": v} for k, v in sorted(allocation.items(), key=lambda kv: -kv[1]) if v
    ]
    plan_inputs = plan.inputs(db, budget_state, net, end, holdings, allocation_rows)
    cds = [a for a in accounts if deposits.kind_of(a) == "cd"]
    maturing = deposits.maturing(deposits.build(db, cds, end), names) if cds else []
    return {
        "demo": False,
        "linked": True,
        "as_of": as_of.isoformat(),
        "net_worth": {"cents": net, "change_today_cents": net - yesterday, "history": history},
        "spending": {"month_cents": spent_month, "last_month_same_day_cents": spent_last, "needs_review": to_check},
        **budget.for_household(budget_state),
        "alerts": [
            alerts.event_json(e)
            for e in db.scalars(
                select(AlertEvent)
                .where(AlertEvent.read_at.is_(None), AlertEvent.fired_at > now() - timedelta(days=ALERT_DAYS))
                .order_by(AlertEvent.fired_at.desc())
                .limit(5)
            )
        ],
        "prices_as_of": (lambda t: t.isoformat() if t else None)(portfolio.prices_as_of(positions)),
        "transactions": transactions,
        "accounts": {"cash": cash, "investments": investments, "debts": debts},
        "holdings": holdings,
        "allocation": allocation_rows,
        "projection": plan.projection(plan_inputs),
        "health": {
            "stale": [
                {"id": c.id, "institution": c.institution, "error": c.last_error}
                for c in connections
                if c.status == "stale"
            ],
            "last_sync_ok": last_run.ok if last_run else None,
            # Uploaded or typed-in accounts whose next statement should be out (reminders.py).
            "uploads": [
                {k: r[k] for k in ("account_id", "name", "provider", "every", "newest", "statement", "days_late")}
                for r in reminders.due(db, reminders.local_today(db))
            ],
            "maturing": maturing,  # CDs to decide about (deposits.maturing)
            "bills": bills.status(db, reminders.local_today(db))["late"],  # utility bills not paid yet (bills.py)
        },
    }, plan_inputs
