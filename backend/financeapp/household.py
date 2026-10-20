"""The household's numbers, built from the database for the app's pages (the same shape demo.py makes up).

Net worth = cash + investments - card and loan balances, using each account's latest balance, and only accounts
marked authoritative (so an account that comes in through two sources is counted once). Transfers between your own
accounts are left out of spending. Budgets, the savings rate and projections arrive in later phases; until then
those parts are null and the pages say so.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp.models import Account, Balance, Category, Connection, HoldingSnapshot, SyncRun, Transaction
from financeapp.sync import today

HISTORY_DAYS = 90
TRANSACTION_DAYS = 62  # always reaches back to the start of last month
ASSET = {"depository", "investment", "other"}
DEBT = {"credit", "loan"}


def display_name(a: Account) -> str:
    name = a.name if a.institution.lower() in a.name.lower() else f"{a.institution} {a.name}"
    return f"{name} ••{a.mask}" if a.mask else name


def signed(a: Account, cents: int) -> int:
    return -cents if a.type in DEBT else cents


def net_worth_history(db: DbSession, accounts: list[Account], end: date) -> list[dict]:
    start = end - timedelta(days=HISTORY_DAYS - 1)
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
            if i >= 0:
                latest[aid] = series[i][1]
        if day >= max(start, first_day):
            history.append({"date": day.isoformat(), "cents": sum(signed(kinds[a], c) for a, c in latest.items())})
        day += timedelta(days=1)
    return history


def build(db: DbSession) -> dict:
    accounts = list(db.scalars(select(Account).where(Account.hidden.is_(False), Account.is_authoritative.is_(True))))
    if not accounts:
        return {"demo": False, "linked": False}
    end = today()
    history = net_worth_history(db, accounts, end)
    net = history[-1]["cents"] if history else 0
    yesterday = history[-2]["cents"] if len(history) > 1 else net

    latest_balance = {}
    for a in accounts:
        b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        latest_balance[a.id] = b

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

    # Holdings: each account's most recent snapshot.
    holdings = []
    invest_ids = [a.id for a in accounts if a.type == "investment"]
    names = {a.id: display_name(a) for a in accounts}
    for aid in invest_ids:
        last = db.scalar(select(func.max(HoldingSnapshot.date)).where(HoldingSnapshot.account_id == aid))
        if last is None:
            continue
        prev = db.scalar(
            select(func.max(HoldingSnapshot.date)).where(HoldingSnapshot.account_id == aid, HoldingSnapshot.date < last)
        )
        prev_prices = (
            {
                h.symbol: h.price
                for h in db.scalars(
                    select(HoldingSnapshot).where(HoldingSnapshot.account_id == aid, HoldingSnapshot.date == prev)
                )
            }
            if prev
            else {}
        )
        for h in db.scalars(
            select(HoldingSnapshot).where(HoldingSnapshot.account_id == aid, HoldingSnapshot.date == last)
        ):
            before = prev_prices.get(h.symbol)
            change = float((h.price - before) / before * 100) if before else 0.0
            holdings.append(
                {
                    "symbol": h.symbol,
                    "name": h.name,
                    "asset_class": h.asset_class,
                    "account": names[aid],
                    "value_cents": h.value_cents,
                    "change_today_percent": round(change, 2),
                    "cost_basis_cents": h.cost_basis_cents,
                    "as_of": last.isoformat(),
                }
            )

    allocation: dict[str, int] = defaultdict(int)
    holdings_by_account: dict[str, int] = defaultdict(int)
    for h in holdings:
        allocation[h["asset_class"]] += h["value_cents"]
        holdings_by_account[h["account"]] += h["value_cents"]
    for row in investments:  # investment accounts with a balance but no holdings detail
        if holdings_by_account.get(row["name"], 0) == 0 and row["cents"]:
            allocation["Other investments"] += row["cents"]
    allocation["Cash"] += sum(r["cents"] for r in cash)

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
    review = sum(1 for t in txns if t.category_source == "none" and is_spending(t))

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

    return {
        "demo": False,
        "linked": True,
        "as_of": as_of.isoformat(),
        "net_worth": {"cents": net, "change_today_cents": net - yesterday, "history": history},
        "spending": {"month_cents": spent_month, "last_month_same_day_cents": spent_last, "needs_review": review},
        "safe_to_spend": None,
        "savings_rate": None,
        "budgets": [],
        "budgets_closest": [],
        "alerts": [],
        "transactions": transactions,
        "accounts": {"cash": cash, "investments": investments, "debts": debts},
        "holdings": holdings,
        "allocation": [
            {"asset_class": k, "cents": v} for k, v in sorted(allocation.items(), key=lambda kv: -kv[1]) if v
        ],
        "projection": None,
        "health": {
            "stale": [
                {"id": c.id, "institution": c.institution, "error": c.last_error}
                for c in connections
                if c.status == "stale"
            ],
            "last_sync_ok": last_run.ok if last_run else None,
        },
    }
