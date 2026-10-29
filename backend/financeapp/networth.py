"""How net worth adds up, and what moved it: the sheet behind Home's Net worth card.

The math: every counted account's balance (household.counted, at live prices like Home), grouped as cash,
investments, retirement and HSA, other assets, cards and loans. Assets minus what's owed is the headline, to the cent.

What moved it, over a window (today, a week, a month, three months, this year, a year): each account's balance now
against its balance at the start of the window, split where the data allows into
- markets: what the shares held at the start gained or lost as prices moved (shares x the price change, from the
  holdings snapshot on or before the start). Shares bought, sold or vested since then aren't market movement.
- everything else ("saving and spending"): pay coming in, spending, card and loan balances, 401(k) contributions,
  vests, dividends. Money moved between two of your own accounts cancels out, since both sides are counted.
An investment account without holdings detail is split with the brokerage's own record of money in and out
(transfers.is_cash_move); with neither, it can't be split and says so.

An account that wasn't synced yet at the start of the window is counted from its first balance (linking an account
isn't a change in net worth), and the window says so. Unvested stock plan shares are listed apart, never counted.

The arithmetic (breakdown) works on plain data, so demo mode runs the same sums on its sample household.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import household, kinds, portfolio
from financeapp.models import Account, Balance, HoldingSnapshot, InvestmentTransaction
from financeapp.transfers import is_cash_move

HISTORY_DAYS = 366
MOVERS = 5  # holdings listed under "Biggest market moves"
STALE_DAYS = 7  # a balance older than this is flagged on its row

# key, label, what it holds; in the order the sheet lists them.
GROUPS = [
    ("cash", "Cash", "Checking, savings and CDs"),
    ("investments", "Investments", "Brokerage accounts you can sell from any time"),
    ("retirement", "Retirement and HSA", "401(k), IRA, HSA and 529: tax-advantaged, meant for later"),
    ("other", "Other assets", "Anything else you count, like a home or car"),
    ("cards", "Credit cards", "What you owe on cards right now"),
    ("loans", "Loans", "Mortgage, car and student loans"),
]
OWED = {"cards", "loans"}
WINDOWS = [("today", "Today"), ("week", "1W"), ("month", "1M"), ("3m", "3M"), ("ytd", "YTD"), ("year", "1Y")]

Holdings = dict[str, tuple[Decimal, Decimal, str]]  # symbol -> (shares, price, name)


@dataclass
class AccountIn:
    id: int
    name: str
    type: str  # depository | credit | loan | investment | other
    subtype: str | None
    now_cents: int  # as stored: a card's balance is positive
    balances: list[tuple[date, int]]  # oldest first
    holdings_now: Holdings = field(default_factory=dict)
    snapshots: list[tuple[date, Holdings]] = field(default_factory=list)  # oldest first
    moves: list[tuple[date, int]] = field(default_factory=list)  # money in (+) or out (-), from the brokerage
    has_activity: bool = False  # the brokerage sends its activity, so `moves` can be trusted
    unvested: list[dict] = field(default_factory=list)  # {"symbol", "name", "shares", "value_cents"}
    # The kind Settings → Accounts lists it as (worked out from the account's own name, not the display name),
    # or None to work it out here.
    kind_set: str | None = None
    synced: date | None = None  # the last balance's day, when `balances` holds a made-up one (yesterday's close)

    @property
    def kind(self) -> str:
        return kinds.kind_of(self.type, self.subtype, self.name, self.kind_set)


def group_of(a: AccountIn) -> str:
    """The same kinds Settings → Accounts lists, added up in fewer groups."""
    return kinds.NET_WORTH_GROUP[a.kind]


def signed(a: AccountIn, cents: int) -> int:
    return -cents if a.type in household.DEBT else cents


def _months_back(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise AssertionError


def window_start(key: str, end: date) -> date:
    """The day whose closing balances the window is measured from."""
    return {
        "today": lambda: end - timedelta(days=1),
        "week": lambda: end - timedelta(days=7),
        "month": lambda: _months_back(end, 1),
        "3m": lambda: _months_back(end, 3),
        "ytd": lambda: date(end.year - 1, 12, 31),
        "year": lambda: end - timedelta(days=365),
    }[key]()


def _on_or_before(series: list[tuple[date, object]], day: date):
    found = None
    for d, v in series:
        if d > day:
            break
        found = (d, v)
    return found


def _market(a: AccountIn, since: date, start_cents: int, end_cents: int) -> tuple[int | None, str, dict[str, int]]:
    """An investment account's change from prices alone, how it was worked out, and the gain by holding."""
    snap = _on_or_before(a.snapshots, since)
    if snap is not None and a.holdings_now:
        by_symbol: dict[str, int] = {}
        for symbol, (shares, price, _) in snap[1].items():
            now = a.holdings_now.get(symbol)
            if now is None or not shares:
                continue
            by_symbol[symbol] = int((shares * (now[1] - price) * 100).to_integral_value())
        return sum(by_symbol.values()), "holdings", by_symbol
    if a.has_activity:
        added = sum(c for d, c in a.moves if since < d)
        return end_cents - start_cents - added, "activity", {}
    return None, "none", {}


def _account_window(a: AccountIn, start: date, end_cents: int) -> dict | None:
    if not a.balances:
        return None
    found = _on_or_before(a.balances, start)
    since, start_cents = (start, found[1]) if found else a.balances[0]
    change = signed(a, end_cents) - signed(a, start_cents)
    market, how, by_symbol = (None, "n/a", {})
    if a.type == "investment":
        market, how, by_symbol = _market(a, since, start_cents, end_cents)
    return {
        "id": a.id,
        "name": a.name,
        "group": group_of(a),
        "start_cents": signed(a, start_cents),
        "end_cents": signed(a, end_cents),
        "change_cents": change,
        "market_cents": market,
        "other_cents": None if market is None and a.type == "investment" else change - (market or 0),
        "split": how,
        "since": since.isoformat(),
        "new": found is None,
        "_by_symbol": by_symbol,
    }


def breakdown(accounts: list[AccountIn], end: date) -> dict:
    """The sheet: the math behind today's net worth, each window's movers, a year of history, and what's left out."""
    groups = []
    rows_by_group: dict[str, list[dict]] = {k: [] for k, _, _ in GROUPS}
    for a in accounts:
        last = a.synced or (a.balances[-1][0] if a.balances else None)
        rows_by_group[group_of(a)].append(
            {
                "id": a.id,
                "name": a.name,
                "kind": kinds.LABEL[a.kind],
                "cents": signed(a, a.now_cents),
                "as_of": last.isoformat() if last else None,
                "stale": last is not None and (end - last).days > STALE_DAYS,
            }
        )
    for key, label, about in GROUPS:
        rows = sorted(rows_by_group[key], key=lambda r: -abs(r["cents"]))
        if rows:
            groups.append(
                {"key": key, "label": label, "about": about, "owed": key in OWED,
                 "cents": sum(r["cents"] for r in rows), "accounts": rows}
            )  # fmt: skip
    total = {g["key"]: g["cents"] for g in groups}
    net = sum(total.values())

    names = {s: n for a in accounts for s, (_, _, n) in a.holdings_now.items()}
    earliest = min((a.balances[0][0] for a in accounts if a.balances), default=end)
    windows = []
    for key, label in WINDOWS:
        start = window_start(key, end)
        rows = [r for a in accounts if (r := _account_window(a, start, a.now_cents)) is not None]
        by_symbol: dict[str, int] = {}
        for r in rows:
            for s, c in r.pop("_by_symbol").items():
                by_symbol[s] = by_symbol.get(s, 0) + c
        new = [r["name"] for r in rows if r["new"] and date.fromisoformat(r["since"]) > earliest]
        rows = [r for r in rows if r["change_cents"] or r["market_cents"]]
        rows.sort(key=lambda r: -abs(r["change_cents"]))
        market = sum(r["market_cents"] or 0 for r in rows)
        unsplit = sum(r["change_cents"] for r in rows if r["market_cents"] is None and r["split"] == "none")
        change = sum(r["change_cents"] for r in rows)
        windows.append(
            {
                "key": key,
                "label": label,
                "start": start.isoformat(),
                # The window reaches back before anything was synced: it shows from the first balance on.
                "since": max(start, earliest).isoformat(),
                "change_cents": change,
                "market_cents": market,
                "saving_cents": change - market - unsplit,
                "unsplit_cents": unsplit,
                "new_accounts": new,
                "accounts": rows,
                "movers": [
                    {"symbol": s, "name": names.get(s, s), "cents": c}
                    for s, c in sorted(by_symbol.items(), key=lambda kv: -abs(kv[1]))[:MOVERS]
                    if c
                ],
            }
        )

    unvested = sorted(
        ({**u, "account": a.name} for a in accounts for u in a.unvested if u["value_cents"] > 0),
        key=lambda u: -u["value_cents"],
    )
    return {
        "cents": net,
        "assets_cents": sum(c for k, c in total.items() if k not in OWED),
        "owed_cents": -sum(c for k, c in total.items() if k in OWED),
        # What you could reach in days without touching retirement money or selling the house: cash and taxable
        # investments, less what's on the cards.
        "within_reach_cents": total.get("cash", 0) + total.get("investments", 0) + total.get("cards", 0),
        "groups": groups,
        "windows": windows,
        "history": _history(accounts, end, net),
        "unvested": {"cents": sum(u["value_cents"] for u in unvested), "holdings": unvested},
    }


def _history(accounts: list[AccountIn], end: date, net: int) -> list[dict]:
    """Net worth each day for the last year, carrying each balance forward, ending at today's live figure. An account
    counts from the start at its first balance, as the windows count it, so the line moves by exactly a window's
    change and linking an account isn't drawn as a jump."""
    first = min((a.balances[0][0] for a in accounts if a.balances), default=end)
    day = max(first, end - timedelta(days=HISTORY_DAYS - 1))
    pointers = {a.id: -1 for a in accounts}
    out = []
    while day <= end:
        cents = 0
        for a in accounts:
            i = pointers[a.id]
            while i + 1 < len(a.balances) and a.balances[i + 1][0] <= day:
                i += 1
            pointers[a.id] = i
            if a.balances:
                cents += signed(a, a.balances[max(i, 0)][1])
        out.append({"date": day.isoformat(), "cents": cents})
        day += timedelta(days=1)
    if out:
        out[-1]["cents"] = net
    return out


# ---- from the database


def build(db: DbSession, end: date) -> dict:
    accounts = household.counted(db)
    if not accounts:
        return {"linked": False}
    latest = household.latest_balances(db, accounts)
    positions = portfolio.positions(db, accounts)
    moved = household.live_moves(latest, positions)
    close = household.last_close(db, accounts, positions, end)
    since = end - timedelta(days=HISTORY_DAYS + 31)
    ins = []
    for a in accounts:
        balances = [
            (d, c)
            for d, c in db.execute(
                select(Balance.date, Balance.current_cents)
                .where(Balance.account_id == a.id, Balance.date <= end)
                .order_by(Balance.date)
            )
        ]
        b = latest[a.id]
        ai = AccountIn(
            a.id, household.display_name(a), a.type, a.subtype,
            (b.current_cents if b else 0) + moved.get(a.id, 0), balances,
            kind_set=kinds.kind_of(a.type, a.subtype, a.name, a.kind),
        )  # fmt: skip
        if a.type == "investment":
            _load_investments(db, a, ai, positions, end, since)
        if a.id in close:  # "today" starts from yesterday's close (household.last_close)
            cents, holdings = close[a.id]
            ai.synced = ai.balances[-1][0]
            ai.balances = household.with_balance(ai.balances, end - timedelta(days=1), cents)
            ai.snapshots = sorted([s for s in ai.snapshots if s[0] != end - timedelta(days=1)]
                                  + [(end - timedelta(days=1), holdings)], key=lambda s: s[0])  # fmt: skip
        ins.append(ai)
    return {"linked": True, **breakdown(ins, end)}


def _load_investments(
    db: DbSession, a: Account, ai: AccountIn, positions: list[portfolio.Position], end: date, since: date
) -> None:
    mine = [p for p in positions if p.account_id == a.id]
    ai.holdings_now = {p.symbol: (p.quantity, p.price, p.name) for p in mine}
    # The holdings each window starts from: the snapshot on or before each window's first day.
    first = ai.balances[0][0] if ai.balances else end
    days = set()
    for key, _ in WINDOWS:
        start = max(window_start(key, end), first)
        mine_before = (HoldingSnapshot.account_id == a.id, HoldingSnapshot.date <= start)
        d = db.scalar(select(func.max(HoldingSnapshot.date)).where(*mine_before))
        if d is not None:
            days.add(d)
    for d in sorted(days):
        rows = db.scalars(select(HoldingSnapshot).where(HoldingSnapshot.account_id == a.id, HoldingSnapshot.date == d))
        ai.snapshots.append((d, {h.symbol: (h.quantity, h.price, h.name) for h in rows}))
    any_activity = select(InvestmentTransaction.id).where(InvestmentTransaction.account_id == a.id).limit(1)
    ai.has_activity = db.scalar(any_activity) is not None
    ai.moves = [
        (t.date, t.amount_cents)
        for t in db.scalars(
            select(InvestmentTransaction).where(
                InvestmentTransaction.account_id == a.id, InvestmentTransaction.date >= since
            )
        )
        if is_cash_move(t)
    ]
    # Stock plan shares not vested yet, at today's price where there is one.
    last = db.scalar(select(func.max(HoldingSnapshot.date)).where(HoldingSnapshot.account_id == a.id))
    if last is None:
        return
    price_now = {p.symbol: p.price for p in mine}
    for h in db.scalars(
        select(HoldingSnapshot).where(
            HoldingSnapshot.account_id == a.id, HoldingSnapshot.date == last, HoldingSnapshot.unvested_quantity > 0
        )
    ):
        price = price_now.get(h.symbol, h.price)
        ai.unvested.append(
            {
                "symbol": h.symbol,
                "name": h.name,
                "shares": float(h.unvested_quantity),
                "value_cents": int((h.unvested_quantity * price * 100).to_integral_value()),
            }
        )
