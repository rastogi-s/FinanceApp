"""A made-up household for demo mode: the CI smoke test and anyone trying the app before linking accounts.

Everything is generated from a fixed seed relative to today, so screenshots stay stable from run to run while the
dates stay current. Money is integer cents, as it will be in the database.
"""

import random
import statistics
from datetime import UTC, date, datetime, timedelta

from financeapp import forecast, plan

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
    ("VTI", "Vanguard Total Stock Market ETF", "US stocks", "Fidelity IRA", 28_700, 0.62),
    ("VTI", "Vanguard Total Stock Market ETF", "US stocks", "E*TRADE", 12_100, 0.62),
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
        "projection": plan.projection(_plan_inputs(today, net_worth)),
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


PACE = {"Groceries": 0.5, "Dining out": 1.0, "Shopping": 0.8, "Transport": 0.9, "Entertainment": 0.3}  # of usual
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
    days = forecast.days_in(month_start)
    left = days - today.day + 1
    for b in lines:
        due = []
        if b["category"] == "Utilities":
            due = [{"merchant": "City Power & Light", "cents": b["limit_cents"] - b["spent_cents"],
                    "day": min(days, max(today.day + 3, 24))}]  # fmt: skip
        pace = round(b["typical_cents"] * PACE.get(b["category"], 0) * left / days)
        projected = b["spent_cents"] + sum(d["cents"] for d in due) + pace
        b["forecast"] = {
            "projected_cents": projected,
            "over_cents": max(projected - b["limit_cents"], 0),
            "status": forecast.status(projected, b["spent_cents"], b["limit_cents"], b["kind"]),
            "due_cents": sum(d["cents"] for d in due),
            "due": due,
            "pace_cents": pace,
            "days_left": left,
        }
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
        "forecast": forecast.summary(lines),
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
                "frequency": "biweekly",
                "deposit": {"date": (paycheck - timedelta(days=14 * i)).isoformat(), "merchant": "Acme Corp Payroll"},
            }
            for i in range(3)
        ],
        "people": [{"id": 0, "name": "Sample"}],
        "schedules": [
            {
                "user_id": 0,
                "person": "Sample",
                "frequency": "biweekly",
                "next_date": (paycheck + timedelta(days=14)).isoformat(),
                "monthly_gross_cents": _cents(4_500 * 26 / 12),
                "monthly_net_cents": _cents(2_600 * 26 / 12),
            }
        ],
        "categories": [
            {"id": i, "name": n, "kind": k, "essential": k == "fixed" or n in ("Groceries", "Transport")}
            for i, (n, k) in enumerate(CATEGORY_NAMES, 1)
            if k in ("fixed", "flexible")
        ],
    }


# ---- the Plan page

SUBSCRIPTIONS = [  # merchant, category, monthly dollars, went up
    ("Peloton", "Subscriptions", 44.00, False),
    ("New York Times", "Subscriptions", 25.00, False),
    ("Netflix", "Subscriptions", 22.99, True),
    ("Spotify", "Subscriptions", 11.99, False),
    ("iCloud+", "Subscriptions", 2.99, False),
]


def _plan_inputs(today: date, net_worth: int) -> dict:
    b = budget(today)
    regular = _cents(statistics.median(spent for *_, spent in SAVINGS))
    essentials = round(regular * 0.62)
    saved = [m["saved_cents"] for m in b["savings_history"]]
    where = "your median month of spending, the last 6 months"

    def level(cents: int, note: str) -> dict:
        return {"yearly_cents": cents, "typed": False, "derived_cents": cents, "note": note}

    return {
        "start_cents": net_worth,
        "year": today.year,
        "monthly_savings_cents": round(sum(saved) / len(saved)),
        "measured_savings_cents": round(sum(saved) / len(saved)),
        "savings_source": {
            "kind": "savings_rate",
            "short": "from your pay stubs and spending",
            "note": "Average of the last 6 months, the months with pay stubs in.",
            "breakdown": [
                {"label": label, "cents": sign * _cents(sum(row[col] for row in SAVINGS) / len(SAVINGS))}
                for label, col, sign in (
                    ("Pay before taxes and other income", 1, 1),
                    ("Taxes", 2, -1),
                    ("Other paycheck deductions (health insurance and the like)", 3, -1),
                    ("Spending", 4, -1),
                )
            ],
        },
        "savings_typed": False,
        "returns": {"low": 3.5, "mid": 5.5, "high": 7.5},
        "returns_source": {
            "typed": False,
            "derived_mid": 5.5,
            "note": "From your mix today: 77% stocks, 9% bonds, 14% cash. Long-run guesses after inflation: stocks "
            "6.5%, bonds 2%, cash 0.5% a year.",
            "mix": {"stocks": 77, "bonds": 9, "cash": 14, "other": 0},
        },
        "withdrawal_rate": 4.0,
        "level": "regular",
        "birth_year": today.year - 36,
        "levels": {
            "lean": level(
                essentials * 12, f"Bills and essentials (rent, groceries, utilities and the like) from {where}."
            ),
            "regular": level(regular * 12, f"From {where}."),
            "fat": level(round(regular * 12 * plan.FAT), f"Regular spending and half again, from {where}."),
        },
        "months_used": 6,
        "emergency": b["emergency"],
    }


def plan_page(today: date | None = None) -> dict:
    """The Plan page in demo mode, matching household()."""
    today = today or datetime.now(UTC).date()
    h = household(today)
    net_worth = h["net_worth"]["cents"]
    rng = random.Random(SEED + 1)  # noqa: S311 - sample data, not security
    # Two years, a point a week: savings plus a bumpy market, ending at today's value.
    history = []
    value = net_worth
    for i in range(105):
        history.append({"date": (today - timedelta(days=7 * i)).isoformat(), "cents": value})
        value -= _cents(1_150 + rng.uniform(-4_500, 5_200))
    history.reverse()
    month_start = today.replace(day=1)
    last_month = _last_month(month_start)
    subs = [
        {
            "merchant": m,
            "category": c,
            "monthly_cents": _cents(d),
            "yearly_cents": _cents(d * 12),
            "last_date": (month_start + timedelta(days=i * 3)).isoformat(),
            "months": 5,
            "went_up": up,
        }
        for i, (m, c, d, up) in enumerate(SUBSCRIPTIONS)
    ]
    return {
        "demo": True,
        "linked": True,
        "net_worth_cents": net_worth,
        "history": history,
        "inputs": _plan_inputs(today, net_worth),
        "projection": h["projection"],
        "ideas": {
            "subscriptions": subs,
            "subscriptions_yearly_cents": sum(s["yearly_cents"] for s in subs),
            "rising": [
                {
                    "category_id": 7,
                    "category": "Dining out",
                    "month": last_month.isoformat(),
                    "spent_cents": _cents(612),
                    "usual_cents": _cents(410),
                    "change_cents": _cents(202),
                }
            ],
        },
    }


# ---- portfolio, holding detail and alerts (Phase 3)

# What sits inside the sample funds (percent of each fund), roughly as their filings say.
S_AND_P = [
    ("NVDA", "NVIDIA Corp", 7.6), ("MSFT", "Microsoft Corp", 6.8), ("AAPL", "Apple Inc.", 6.5),
    ("AMZN", "Amazon.com, Inc.", 3.9), ("GOOGL", "Alphabet Inc.", 3.8), ("META", "Meta Platforms, Inc.", 2.9),
    ("AVGO", "Broadcom Inc.", 2.6), ("TSLA", "Tesla, Inc.", 2.0), ("BRK.B", "Berkshire Hathaway Inc.", 1.6),
    ("JPM", "JPMorgan Chase & Co.", 1.5), ("LLY", "Eli Lilly and Company", 1.3), ("V", "Visa Inc.", 1.1),
    ("MA", "Mastercard Inc.", 0.9), ("XOM", "Exxon Mobil Corp", 0.9), ("COST", "Costco Wholesale Corp", 0.9),
    ("WMT", "Walmart Inc.", 0.9), ("NFLX", "Netflix, Inc.", 0.8), ("UNH", "UnitedHealth Group Inc.", 0.8),
    ("HD", "The Home Depot, Inc.", 0.7), ("PG", "Procter & Gamble Co.", 0.7),
]  # fmt: skip
INTERNATIONAL = [
    ("TSM", "Taiwan Semiconductor Manufacturing", 2.6), (None, "Tencent Holdings Ltd", 1.2),
    ("ASML", "ASML Holding NV", 1.0), (None, "Samsung Electronics Co Ltd", 0.9), ("SAP", "SAP SE", 0.8),
    (None, "Nestle SA", 0.6), ("NVO", "Novo Nordisk A/S", 0.6), ("TM", "Toyota Motor Corp", 0.6),
    ("AZN", "AstraZeneca PLC", 0.6), ("SHOP", "Shopify Inc.", 0.5),
]  # fmt: skip
SAMPLE_FUNDS = {
    # symbol: (holdings, share of the fund in stocks, number of holdings, constituents)
    "VTI": ("Vanguard Total Stock Market Index Fund", 99.9, 3612, [(s, n, round(w * 0.86, 2)) for s, n, w in S_AND_P]),
    "FXAIX": ("Fidelity 500 Index Fund", 99.7, 504, S_AND_P),
    "VXUS": ("Vanguard Total International Stock Index Fund", 99.2, 8541, INTERNATIONAL),
    "BND": ("Vanguard Total Bond Market Index Fund", 0.0, 11203, []),
}


def _sample_exposure(today: date) -> dict:
    total = sum(h[4] for h in HOLDINGS)
    as_of = (today.replace(day=1) - timedelta(days=45)).replace(day=1) - timedelta(days=1)  # a quarter-end, about
    companies: dict[str, dict] = {}

    def add(symbol: str | None, name: str, dollars: float, via: str) -> None:
        key = symbol or name
        c = companies.setdefault(key, {"key": key, "symbol": symbol, "name": name, "cents": 0.0, "sources": {}})
        c["cents"] += dollars * 100
        c["sources"][via] = c["sources"].get(via, 0.0) + dollars * 100

    funds = []
    for symbol, name, cls, _account, dollars, _ in HOLDINGS:
        if symbol in SAMPLE_FUNDS:
            fname, equity, count, rows = SAMPLE_FUNDS[symbol]
            for s, n, w in rows:
                add(s, n, dollars * w / 100, symbol)
            funds.append(
                {
                    "symbol": symbol,
                    "name": fname,
                    "cents": _cents(dollars),
                    "percent": round(dollars / total * 100, 3),
                    "status": "ok",
                    "error": None,
                    "proxy_of": None,
                    "as_of": as_of.isoformat(),
                    "holdings": count,
                    "equity_percent": equity,
                    "top": [{"symbol": s, "name": n, "weight_percent": w} for s, n, w in rows[:10]],
                }
            )
        elif cls == "US stocks":
            add(symbol, name, dollars, "direct")

    def pct(cents: float) -> float:
        return round(cents / (total * 100) * 100, 3)

    rows = sorted(companies.values(), key=lambda c: -c["cents"])
    return {
        "total_cents": _cents(total),
        "companies": [
            {
                "key": c["key"],
                "symbol": c["symbol"],
                "name": c["name"],
                "cents": round(c["cents"]),
                "percent": pct(c["cents"]),
                "direct_percent": pct(c["sources"].get("direct", 0.0)),
                "sources": sorted(
                    ({"symbol": s, "cents": round(v), "percent": pct(v)} for s, v in c["sources"].items()),
                    key=lambda s: -s["cents"],
                ),
            }
            for c in rows
        ],
        "funds": funds,
        "looked_through_percent": round(sum(h[4] for h in HOLDINGS if h[0] in SAMPLE_FUNDS) / total * 100, 1),
        "other_percent": round(sum(h[4] for h in HOLDINGS if h[2] == "Cash") / total * 100, 3),
        "oldest_as_of": as_of.isoformat(),
    }


def portfolio(today: date | None = None) -> dict:
    today = today or datetime.now(UTC).date()
    total = sum(h[4] for h in HOLDINGS)
    by_symbol: dict[str, dict] = {}
    for symbol, name, cls, account, dollars, _ in HOLDINGS:
        row = by_symbol.setdefault(
            symbol, {"symbol": symbol, "name": name, "asset_class": cls, "value_cents": 0, "accounts": []}
        )
        row["value_cents"] += _cents(dollars)
        row["accounts"].append({"account": account, "value_cents": _cents(dollars)})
    rows = sorted(by_symbol.values(), key=lambda r: -r["value_cents"])
    for r in rows:
        r["percent"] = round(r["value_cents"] / _cents(total) * 100, 2)
    return {
        "total_cents": _cents(total),
        "positions": rows,
        "top5_percent": round(sum(r["value_cents"] for r in rows[:5]) / _cents(total) * 100, 1),
        "largest": rows[0]["symbol"],
        "exposure": _sample_exposure(today),
        "prices_as_of": datetime.combine(today, datetime.min.time(), UTC).replace(hour=19, minute=45).isoformat(),
        "market_open": False,
        "sources": {"quotes": "yahoo", "lookthrough": True, "fundamentals": False},
    }


SAMPLE_PRICES = {"VTI": 318.40, "VXUS": 71.20, "AAPL": 339.50, "MSFT": 512.30, "FXAIX": 268.06, "BND": 74.10}


def security(symbol: str, range_: str, today: date | None = None) -> dict:
    """A holding's detail page with a made-up but steady price history."""
    today = today or datetime.now(UTC).date()
    held = [h for h in HOLDINGS if h[0] == symbol]
    price = SAMPLE_PRICES.get(symbol, 100.0)
    rng = random.Random(f"{SEED}:{symbol}:{range_}")  # noqa: S311 - sample data, not security
    steps, seconds = {
        "1d": (78, 300),
        "5d": (65, 1800),
        "1m": (22, 86400),
        "6m": (126, 86400),
        "1y": (252, 86400),
        "5y": (260, 604800),
    }[range_]
    end = int(datetime.combine(today, datetime.min.time(), UTC).replace(hour=20).timestamp())
    drift = {"1d": 0.0004, "5d": 0.001, "1m": 0.002, "6m": 0.004, "1y": 0.006, "5y": 0.012}[range_]
    points, p = [], price
    for i in range(steps):
        points.append([end - i * seconds, round(p, 2)])
        p = p / (1 + drift * rng.uniform(-0.6, 1.4) + rng.gauss(0, drift * 1.6))
    points.reverse()
    change = next((h[5] for h in held), 0.4)
    fund = next((f for f in _sample_exposure(today)["funds"] if f["symbol"] == symbol), None)
    exposure = next((c for c in _sample_exposure(today)["companies"] if c["symbol"] == symbol), None)
    total = sum(h[4] for h in HOLDINGS)
    value = sum(h[4] for h in held)
    is_fund = symbol in SAMPLE_FUNDS
    return {
        "symbol": symbol,
        "name": held[0][1] if held else symbol,
        "type": "ETF" if is_fund else "EQUITY",
        "is_fund": is_fund,
        "exchange": "NasdaqGS" if not is_fund else "NYSEArca",
        "currency": "USD",
        "price": price,
        "previous_close": round(price / (1 + change / 100), 2),
        "change_percent": change,
        "price_at": end,
        "range": range_,
        "points": points,
        "stats": {
            "day_low": round(price * 0.991, 2),
            "day_high": round(price * 1.006, 2),
            "year_low": round(price * 0.78, 2),
            "year_high": round(price * 1.03, 2),
            "volume": 41_250_000 if not is_fund else 3_870_000,
            "dividend_yield": 1.31 if is_fund else 0.44,
        },
        "position": {
            "quantity": round(value / price, 3),
            "value_cents": _cents(value),
            "cost_basis_cents": _cents(value * 0.74),
            "gain_cents": _cents(value * 0.26),
            "gain_percent": 35.14,
            "percent_of_portfolio": round(value / total * 100, 2),
            "accounts": [
                {"account": h[3], "quantity": round(h[4] / price, 3), "value_cents": _cents(h[4])} for h in held
            ],
            "as_of": today.isoformat(),
        }
        if held
        else None,
        "exposure": exposure,
        "fund": fund,
        "rules": [r for r in alerts()["rules"] if r["params"].get("symbol") == symbol],
        "market_data": True,
        "sources": {"quotes": "yahoo", "lookthrough": True, "fundamentals": False},
    }


def alerts() -> dict:
    """Settings → Alerts in demo mode."""
    at = datetime.now(UTC)
    return {
        "rules": [
            {
                "id": 1,
                "kind": "budget",
                "params": {"category_id": None, "percent": 90},
                "enabled": True,
                "summary": "Everyday spending reaches 90% of its budget",
                "triggered": False,
            },
            {
                "id": 5,
                "kind": "budget",
                "params": {"category_id": None, "forecast": True},
                "enabled": True,
                "summary": "Any category is on pace to go over its budget",
                "triggered": False,
            },
            {
                "id": 2,
                "kind": "concentration",
                "params": {"percent": 10},
                "enabled": True,
                "summary": "Any one company reaches 10% of your portfolio",
                "triggered": False,
            },
            {
                "id": 6,
                "kind": "dip",
                "params": {"symbol": None, "from_high_percent": 10},
                "enabled": True,
                "summary": "Anything you hold falls 10% under its 52-week high, and each 10% more",
                "triggered": False,
            },
            {
                "id": 7,
                "kind": "dip",
                "params": {"symbol": None, "below_average": 200},
                "enabled": True,
                "summary": "Anything you hold drops under its 200-day average",
                "triggered": False,
            },
            {
                "id": 3,
                "kind": "price",
                "params": {"symbol": "AAPL", "above": 350},
                "enabled": True,
                "summary": "AAPL goes above $350.00",
                "triggered": False,
            },
            {
                "id": 4,
                "kind": "price",
                "params": {"symbol": "VTI", "move_percent": 3},
                "enabled": True,
                "summary": "VTI moves 3% or more in a day",
                "triggered": False,
            },
        ],
        "events": [
            {
                "id": "a1",
                "kind": "budget",
                "title": "Dining out is at 93% of its budget",
                "body": "Sent to your phone.",
                "at": (at - timedelta(hours=3)).isoformat(),
                "status": "sent",
                "channel": "ntfy",
                "read": False,
            },
            {
                "id": "a2",
                "kind": "price",
                "title": "AAPL is up 1.35% today",
                "body": "AAPL is at $339.50.",
                "at": (at - timedelta(hours=5)).isoformat(),
                "status": "sent",
                "channel": "ntfy",
                "read": False,
            },
            {
                "id": "a4",
                "kind": "dip",
                "title": "MSFT is 11% under its 52-week high",
                "body": "MSFT is at $496.20; its 52-week high is $555.45. Information only, not advice.",
                "at": (at - timedelta(days=2)).isoformat(),
                "status": "sent",
                "channel": "ntfy",
                "read": True,
            },
            {
                "id": "a3",
                "kind": "concentration",
                "title": "NVDA is 5.2% of your portfolio",
                "body": "Counting what's inside your funds.",
                "at": (at - timedelta(days=4)).isoformat(),
                "status": "sent",
                "channel": "ntfy",
                "read": True,
            },
        ],
        "quiet": {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "America/New_York"},
        "channels": {"ntfy": True, "email": False},
        "symbols": sorted({h[0] for h in HOLDINGS if h[2] != "Cash"}),
        "categories": [
            {"id": i, "name": n} for i, (n, k) in enumerate(CATEGORY_NAMES, 1) if k in ("fixed", "flexible")
        ],
    }
