"""A made-up household for demo mode: the CI smoke test and anyone trying the app before linking accounts.

Everything is generated from a fixed seed relative to today, so screenshots stay stable from run to run while the
dates stay current. Money is integer cents, as it will be in the database.
"""

import random
from datetime import UTC, date, datetime, timedelta

SEED = 20261019

BUDGET = [
    # category, kind, monthly limit (dollars), share of the limit spent so far this month
    ("Groceries", "flexible", 900, 0.82),
    ("Dining out", "flexible", 450, 0.93),
    ("Shopping", "flexible", 400, 0.61),
    ("Transport", "flexible", 300, 0.47),
    ("Entertainment", "flexible", 200, 0.88),
    ("Rent", "fixed", 2850, 1.0),
    ("Utilities", "fixed", 240, 0.71),
    ("Insurance", "fixed", 310, 1.0),
    ("Subscriptions", "fixed", 85, 1.0),
]

MERCHANTS = [
    ("Trader Joe's", "Groceries", 40, 140),
    ("Whole Foods", "Groceries", 25, 110),
    ("Sweetgreen", "Dining out", 14, 32),
    ("Blue Bottle Coffee", "Dining out", 5, 12),
    ("Target", "Shopping", 20, 90),
    ("Shell", "Transport", 35, 60),
    ("Lyft", "Transport", 12, 38),
    ("AMC Theatres", "Entertainment", 18, 45),
    ("Spotify", "Subscriptions", 12, 12),
]

HOLDINGS = [
    # symbol, name, asset class, account, value (dollars), change today (%)
    ("VTI", "Vanguard Total Stock Market ETF", "US stocks", "Fidelity 401(k)", 182_400, 0.62),
    ("VXUS", "Vanguard Total International ETF", "International stocks", "Fidelity 401(k)", 61_300, -0.21),
    ("AAPL", "Apple", "US stocks", "E*TRADE", 48_900, 1.35),
    ("MSFT", "Microsoft", "US stocks", "E*TRADE", 37_200, 0.48),
    ("FXAIX", "Fidelity 500 Index Fund", "US stocks", "Fidelity IRA", 74_800, 0.55),
    ("BND", "Vanguard Total Bond Market ETF", "Bonds", "Fidelity IRA", 41_600, 0.08),
    ("HSA cash", "HealthEquity cash", "Cash", "HealthEquity HSA", 6_200, 0.0),
]

CASH = [("Chase checking", 14_350), ("Ally savings", 38_900)]
DEBTS = [("Amex Gold", 2_140), ("Chase Sapphire", 860)]


def _cents(dollars: float) -> int:
    return round(dollars * 100)


def household(today: date | None = None) -> dict:
    today = today or datetime.now(UTC).date()
    rng = random.Random(SEED)  # noqa: S311 - sample data, not security
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    days_left = (next_month - today).days

    investments = sum(h[4] for h in HOLDINGS)
    cash = sum(c[1] for c in CASH)
    debts = sum(d[1] for d in DEBTS)
    net_worth = _cents(investments + cash - debts)
    change_today = sum(_cents(h[4] * h[5] / 100) for h in HOLDINGS)

    # A gently rising, slightly noisy 90-day history that ends at today's value.
    history = []
    value = net_worth
    for i in range(90):
        history.append({"date": (today - timedelta(days=i)).isoformat(), "cents": value})
        value -= _cents(rng.uniform(-1800, 2600))
    history.reverse()

    budgets = [
        {
            "category": name,
            "kind": kind,
            "limit_cents": _cents(limit),
            "spent_cents": _cents(limit * share),
        }
        for name, kind, limit, share in BUDGET
    ]
    flexible_left = sum(b["limit_cents"] - b["spent_cents"] for b in budgets if b["kind"] == "flexible")
    closest = sorted(
        (b for b in budgets if b["kind"] == "flexible"),
        key=lambda b: b["spent_cents"] / b["limit_cents"],
        reverse=True,
    )[:3]

    transactions = []
    for i in range(24):
        merchant, category, low, high = MERCHANTS[rng.randrange(len(MERCHANTS))]
        day = today - timedelta(days=i // 2)
        transactions.append(
            {
                "id": f"demo-{i}",
                "date": day.isoformat(),
                "merchant": merchant,
                "category": category,
                "amount_cents": -_cents(round(rng.uniform(low, high), 2)),
                "account": "Amex Gold" if i % 3 else "Chase Sapphire",
            }
        )
    transactions.insert(
        3,
        {
            "id": "demo-transfer",
            "date": (today - timedelta(days=1)).isoformat(),
            "merchant": "Transfer to Ally savings",
            "category": "Transfer",
            "amount_cents": -_cents(1500),
            "account": "Chase checking",
            "transfer": True,
        },
    )

    by_class: dict[str, int] = {}
    for _, _, asset_class, _, dollars, _ in HOLDINGS:
        by_class[asset_class] = by_class.get(asset_class, 0) + _cents(dollars)
    by_class["Cash"] = by_class.get("Cash", 0) + _cents(cash)

    # Projection: today's net worth growing with monthly savings, at 4%, 6% and 8% a year.
    monthly_savings = 4_200
    bands = []
    for years in range(0, 26):
        point = {"year": today.year + years}
        for label, rate in (("low", 0.04), ("mid", 0.06), ("high", 0.08)):
            r = rate / 12
            months = years * 12
            grown = net_worth / 100 * (1 + r) ** months + monthly_savings * (((1 + r) ** months - 1) / r)
            point[label] = _cents(grown)
        bands.append(point)

    return {
        "demo": True,
        "linked": True,
        "as_of": datetime.combine(today, datetime.min.time(), UTC).replace(hour=6).isoformat(),
        "net_worth": {"cents": net_worth, "change_today_cents": change_today, "history": history},
        "safe_to_spend": {"cents": flexible_left, "days_left": days_left},
        "savings_rate": {"percent": 31.4, "month": month_start.isoformat()},
        "budgets": budgets,
        "budgets_closest": closest,
        "alerts": [
            {
                "id": "a1",
                "kind": "budget",
                "title": "Dining out is at 93%",
                "body": f"{days_left} days left this month.",
                "at": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
            },
            {
                "id": "a2",
                "kind": "price",
                "title": "AAPL up 1.35% today",
                "body": "Your largest single stock.",
                "at": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
            },
        ],
        "transactions": transactions,
        "accounts": {
            "cash": [{"name": n, "cents": _cents(v)} for n, v in CASH],
            "debts": [{"name": n, "cents": _cents(v)} for n, v in DEBTS],
        },
        "holdings": [
            {
                "symbol": s,
                "name": n,
                "asset_class": c,
                "account": a,
                "value_cents": _cents(v),
                "change_today_percent": p,
            }
            for s, n, c, a, v, p in HOLDINGS
        ],
        "allocation": [{"asset_class": k, "cents": v} for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])],
        "projection": {"monthly_savings_cents": _cents(monthly_savings), "bands": bands},
    }


def empty() -> dict:
    """What a fresh install with no linked accounts returns."""
    return {"demo": False, "linked": False}
