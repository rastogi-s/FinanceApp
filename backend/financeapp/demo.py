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
        "savings_rate": {"percent": 31.4, "month": _last_month(month_start).isoformat(), "basis": "gross"},
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
        "spending": {
            "month_cents": sum(-t["amount_cents"] for t in transactions if not t.get("transfer")),
            "last_month_same_day_cents": _cents(2_310),
            "needs_review": 1,
        },
        "accounts": {
            "cash": [
                {"id": i, "name": n, "type": "depository", "cents": _cents(v), "as_of": today.isoformat()}
                for i, (n, v) in enumerate(CASH, 1)
            ],
            "investments": [
                {"id": 10 + i, "name": a, "type": "investment", "cents": _cents(v), "as_of": today.isoformat()}
                for i, (a, v) in enumerate(_by_account().items())
            ],
            "debts": [
                {"id": 20 + i, "name": n, "type": "credit", "cents": _cents(v), "as_of": today.isoformat()}
                for i, (n, v) in enumerate(DEBTS)
            ],
        },
        "health": {"stale": [], "last_sync_ok": True},
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


def _last_month(month_start: date) -> date:
    return (month_start - timedelta(days=1)).replace(day=1)


def _by_account() -> dict[str, float]:
    totals: dict[str, float] = {}
    for _, _, _, account, dollars, _ in HOLDINGS:
        totals[account] = totals.get(account, 0) + dollars
    return totals


CATEGORY_NAMES = [
    ("Paycheck", "income"),
    ("Rent", "fixed"),
    ("Utilities", "fixed"),
    ("Insurance", "fixed"),
    ("Subscriptions", "fixed"),
    ("Groceries", "flexible"),
    ("Dining out", "flexible"),
    ("Shopping", "flexible"),
    ("Transport", "flexible"),
    ("Entertainment", "flexible"),
    ("Transfer", "transfer"),
    ("Uncategorized", "other"),
]


def categories() -> list[dict]:
    return [{"id": i, "name": n, "kind": k} for i, (n, k) in enumerate(CATEGORY_NAMES, 1)]


def connections() -> dict:
    """The Settings → Accounts page in demo mode."""
    today = datetime.now(UTC)
    synced = today.replace(hour=6, minute=2, second=0, microsecond=0).isoformat()

    def account(i: int, name: str, kind: str, cents: int, mask: str) -> dict:
        return {
            "id": i,
            "name": name,
            "display_name": f"{name} ••{mask}",
            "type": kind,
            "subtype": None,
            "mask": mask,
            "hidden": False,
            "is_authoritative": True,
            "owner_user_id": 0,
            "balance_cents": cents,
            "balance_date": today.date().isoformat(),
            "statement_cents": None,
            "statement_date": None,
            "statement_diff_percent": None,
        }

    def connection(i: int, provider: str, institution: str, accounts: list[dict], status: str = "active") -> dict:
        return {
            "id": i,
            "provider": provider,
            "institution": institution,
            "status": status,
            "last_synced_at": synced,
            "last_error": None,
            "created_at": synced,
            "accounts": accounts,
        }

    by_account = _by_account()
    return {
        "connections": [
            connection(
                1,
                "plaid",
                "Chase",
                [
                    account(1, "Chase checking", "depository", _cents(14_350), "4417"),
                    account(2, "Chase Sapphire", "credit", _cents(860), "9012"),
                ],
            ),
            connection(2, "plaid", "Ally", [account(3, "Ally savings", "depository", _cents(38_900), "2290")]),
            connection(3, "plaid", "American Express", [account(4, "Amex Gold", "credit", _cents(2_140), "1008")]),
            connection(
                4,
                "snaptrade",
                "E*TRADE, Fidelity",
                [
                    account(5, "E*TRADE", "investment", _cents(by_account["E*TRADE"]), "6621"),
                    account(6, "Fidelity IRA", "investment", _cents(by_account["Fidelity IRA"]), "3305"),
                ],
            ),
            connection(
                5,
                "plaid",
                "Fidelity NetBenefits",
                [account(7, "Fidelity 401(k)", "investment", _cents(by_account["Fidelity 401(k)"]), "7781")],
            ),
            connection(
                6,
                "file",
                "HealthEquity",
                [account(8, "HealthEquity HSA", "investment", _cents(by_account["HealthEquity HSA"]), "0456")],
            ),
        ],
        "revoked": [],
        "providers": {"plaid": True, "snaptrade": True},
        "people": [{"id": 0, "name": "Sample"}],
        "sync": {"last_at": synced, "last_ok": True, "clean_days": 7, "running": False},
    }


def empty() -> dict:
    """What a fresh install with no linked accounts returns."""
    return {"demo": False, "linked": False}


TYPICAL = {"Groceries": 840, "Dining out": 410, "Shopping": 360, "Transport": 270, "Entertainment": 180}
SAVINGS = [  # month offset, gross, taxes, deductions, spending (dollars): the last full month is 31.4%
    (-6, 18_000, 4_300, 600, 8_310),
    (-5, 18_000, 4_300, 600, 7_920),
    (-4, 18_000, 4_300, 600, 8_640),
    (-3, 18_000, 4_300, 600, 7_380),
    (-2, 18_000, 4_300, 600, 7_710),
    (-1, 18_000, 4_300, 600, 7_450),
]


def budget(today: date | None = None) -> dict:
    """The Budget page in demo mode, matching household()."""
    today = today or datetime.now(UTC).date()
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    ids = {name: i for i, (name, _) in enumerate(CATEGORY_NAMES, 1)}
    lines = [
        {
            "category_id": ids[name],
            "category": name,
            "kind": kind,
            "essential": kind == "fixed" or name in ("Groceries", "Transport"),
            "limit_cents": _cents(limit),
            "spent_cents": _cents(limit * share),
            "typical_cents": _cents(TYPICAL.get(name, limit)),
        }
        for name, kind, limit, share in BUDGET
    ]
    flexible_left = sum(b["limit_cents"] - b["spent_cents"] for b in lines if b["kind"] == "flexible")

    def month(offset: int) -> date:
        m = month_start.year * 12 + month_start.month - 1 + offset
        return date(m // 12, m % 12 + 1, 1)

    history = [
        {
            "month": month(offset).isoformat(),
            "percent": round((gross - taxes - other - spent) / gross * 100, 1),
            "basis": "gross",
            "income_cents": _cents(gross),
            "taxes_cents": _cents(taxes),
            "deductions_cents": _cents(other),
            "spending_cents": _cents(spent),
            "saved_cents": _cents(gross - taxes - other - spent),
            "match_cents": 0,
            "stubs": 4,
        }
        for offset, gross, taxes, other, spent in SAVINGS
    ]
    essentials = sum(b["limit_cents"] for b in lines if b["essential"])
    stamp = datetime.combine(month(-1), datetime.min.time(), UTC).replace(day=2, hour=20).isoformat()
    paycheck = month(0) - timedelta(days=1)
    return {
        "saved": True,
        "version": {
            "id": 2,
            "source": "edit",
            "by": "Sample",
            "at": stamp,
            "total_cents": sum(b["limit_cents"] for b in lines),
        },
        "months_used": 6,
        "rough": False,
        "month": month_start.isoformat(),
        "lines": lines,
        "unbudgeted": [{"category_id": 12, "category": "Uncategorized", "kind": "other", "spent_cents": _cents(38)}],
        "safe_to_spend": {"cents": flexible_left - _cents(38), "days_left": (next_month - today).days},
        "savings_rate": history[-1],
        "savings_history": history,
        "emergency": {
            "months": 6,
            "monthly_cents": essentials,
            "target_cents": essentials * 6,
            "have_cents": _cents(sum(v for _, v in CASH)),
        },
        "include_match": False,
        "versions": [
            {
                "id": 2,
                "source": "edit",
                "by": "Sample",
                "at": stamp,
                "total_cents": sum(b["limit_cents"] for b in lines),
            },
            {"id": 1, "source": "draft", "by": "Sample", "at": stamp, "total_cents": _cents(5_610)},
        ],
        "paychecks": [
            {
                "id": i,
                "user_id": 0,
                "person": "Sample",
                "date": (paycheck - timedelta(days=14 * i)).isoformat(),
                "gross_cents": _cents(4_500),
                "taxes_cents": _cents(1_075),
                "savings_cents": _cents(675),
                "net_cents": _cents(2_600),
                "employer_match_cents": _cents(225),
                "deposit": {"date": (paycheck - timedelta(days=14 * i)).isoformat(), "merchant": "Acme Corp Payroll"},
            }
            for i in range(3)
        ],
        "people": [{"id": 0, "name": "Sample"}],
        "categories": [
            {"id": i, "name": n, "kind": k, "essential": k == "fixed" or n in ("Groceries", "Transport")}
            for i, (n, k) in enumerate(CATEGORY_NAMES, 1)
            if k in ("fixed", "flexible")
        ],
    }
