"""The Plan page: where today's savings lead, when work becomes optional, and ideas for saving more.

Projection. Today's net worth grows each month at a yearly return after inflation, plus the monthly savings,
for 30 years. Three bands, because no one knows future returns: 4%, 6% and 8% a year. Monthly savings is the
average of what the true savings rate (budget.py) says was saved in the last few full months, unless the
household typed in its own number.

Financial independence. The number to reach is a year of spending divided by the withdrawal rate (4% by
default: $60k a year needs $1.5M). Three spending levels, made from the household's own budget so they update by
themselves: lean = bills and essentials only, regular = the median month of spending, fat = regular x 1.5. Any
of them can be set to a fixed amount instead.

Ideas. Subscriptions are merchants charged about the same amount in at least three of the last few months.
A rising category is one whose last full month came in well over its usual month. Both are plain arithmetic on
the household's own transactions; no AI and nothing leaves the server.

Everything here is computed again on every request; the only thing stored is the household's choices
(the "plan" preference).
"""

import statistics
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session as DbSession

from financeapp import budget
from financeapp.categorize import normalize_merchant
from financeapp.models import Preference

RETURNS = {"low": 4.0, "mid": 6.0, "high": 8.0}  # % a year after inflation
YEARS = 30
MAX_YEARS = 60  # looking for the year the target is reached stops here
LEVELS = ("lean", "regular", "fat")
FAT = 1.5
SAVINGS_MONTHS = 6
DEFAULTS: dict = {"withdrawal_rate": 4.0, "level": "regular", "birth_year": None, "savings_cents": None, "spending": {}}

IDEA_MONTHS = 5  # how far back subscriptions are looked for (the current month plus four full ones)
SUBSCRIPTION_MIN_MONTHS = 3
SUBSCRIPTION_SPREAD = 0.15  # each charge within 15% of the usual one
SUBSCRIPTION_NEW_PRICE = 0.50  # the newest charge may have gone up or down this much
RISING_SHARE = 0.20  # 20% over the usual month...
RISING_CENTS = 50_00  # ...and at least $50 more
RISING_BASE_MONTHS = 3


def preferences(db: DbSession) -> dict:
    p = db.get(Preference, "plan")
    value = p.value if p else {}
    return {**DEFAULTS, **value, "spending": {**value.get("spending", {})}}


def set_preferences(db: DbSession, changes: dict) -> dict:
    current = preferences(db)
    spending = {**current["spending"], **changes.pop("spending", {})}
    value = {**current, **changes, "spending": {k: v for k, v in spending.items() if v is not None}}
    db.merge(Preference(key="plan", value=value))
    db.flush()
    return value


# ---- the arithmetic (frontend/src/lib/plan.ts does the same, so the what-if sliders answer instantly)


def grow(start: int, monthly: int, rate_percent: float, months: int) -> int:
    """Where `start` gets to after `months`, adding `monthly` at the end of each month, at a yearly return."""
    r = rate_percent / 100 / 12
    if r == 0:
        return round(start + monthly * months)
    f = (1 + r) ** months
    return round(start * f + monthly * (f - 1) / r)


def months_to(target: int, start: int, monthly: int, rate_percent: float) -> int | None:
    """Months until the target is reached (0 if it already is), or None if not within MAX_YEARS."""
    if start >= target:
        return 0
    value = float(start)
    r = rate_percent / 100 / 12
    for m in range(1, MAX_YEARS * 12 + 1):
        value = value * (1 + r) + monthly
        if value >= target:
            return m
    return None


def bands(start: int, monthly: int, year: int, years: int = YEARS) -> list[dict]:
    return [
        {"year": year + y, **{k: grow(start, monthly, rate, y * 12) for k, rate in RETURNS.items()}}
        for y in range(years + 1)
    ]


def target_for(yearly_spending: int, withdrawal_rate: float) -> int:
    return round(yearly_spending / (withdrawal_rate / 100))


# ---- the household's own numbers


def spending_levels(state: dict, ledger: budget.Ledger, months: list[date], overrides: dict) -> dict:
    """Yearly spending at each level, and where each came from (the budget, or a number someone typed)."""
    essentials = sum(line["limit_cents"] for line in state["lines"] if line["essential"])
    per_month = [sum(v for (_, m), v in ledger.spent.items() if m == month) for month in months]
    regular = round(statistics.median(per_month)) if per_month else 0
    made = {"lean": essentials * 12, "regular": regular * 12, "fat": round(regular * 12 * FAT)}
    out = {}
    for level in LEVELS:
        typed = overrides.get(level)
        out[level] = {"yearly_cents": typed if typed is not None else made[level], "typed": typed is not None}
    return out


def monthly_savings(state: dict) -> int | None:
    recent = state["savings_history"][-SAVINGS_MONTHS:]
    if not recent:
        return None
    return round(sum(m["saved_cents"] for m in recent) / len(recent))


def inputs(db: DbSession, state: dict, net_worth: int, on: date) -> dict:
    """What the projection and the what-if sliders start from."""
    prefs = preferences(db)
    accounts = budget.counted_accounts(db)
    months = budget.full_months(db, [a.id for a in accounts], on)
    ledger = budget.Ledger(db, accounts, months[0] if months else budget.month_start(on))
    measured = monthly_savings(state)
    typed = prefs["savings_cents"]
    return {
        "start_cents": net_worth,
        "year": on.year,
        "monthly_savings_cents": typed if typed is not None else measured,
        "measured_savings_cents": measured,
        "savings_months": len(state["savings_history"][-SAVINGS_MONTHS:]),
        "savings_typed": typed is not None,
        "returns": RETURNS,
        "withdrawal_rate": prefs["withdrawal_rate"],
        "level": prefs["level"],
        "birth_year": prefs["birth_year"],
        "levels": spending_levels(state, ledger, months, prefs["spending"]),
        "months_used": len(months),
        "emergency": state["emergency"],
    }


def projection(i: dict) -> dict | None:
    """The projection in household.build's shape (the brief and Home read it), or None with nothing to go on."""
    monthly = i["monthly_savings_cents"]
    if monthly is None:
        return None
    yearly = i["levels"][i["level"]]["yearly_cents"]
    target = target_for(yearly, i["withdrawal_rate"]) if yearly > 0 else None
    fire = None
    if target is not None:
        reached = {k: months_to(target, i["start_cents"], monthly, rate) for k, rate in i["returns"].items()}
        fire = {k: (i["year"] * 12 + m) // 12 if m is not None else None for k, m in reached.items()}
    return {
        "monthly_savings_cents": monthly,
        "bands": bands(i["start_cents"], monthly, i["year"]),
        "target_cents": target,
        "fire_year": fire,
    }


# ---- ideas


def subscriptions(ledger: budget.Ledger, on: date) -> list[dict]:
    first = budget.add_months(budget.month_start(on), -(IDEA_MONTHS - 1))
    charges: dict[str, list] = defaultdict(list)
    for t in ledger.txns:
        if t.date >= first and t.amount_cents < 0 and not t.pending and ledger.is_spending(t):
            charges[normalize_merchant(t.merchant)].append(t)
    found = []
    for key, txns in charges.items():
        by_month: dict[date, int] = defaultdict(int)
        for t in txns:
            by_month[budget.month_start(t.date)] -= t.amount_cents
        # One charge a month, every month it shows up: a shop visited weekly isn't a subscription.
        if len(by_month) < SUBSCRIPTION_MIN_MONTHS or len(txns) != len(by_month) or not key:
            continue
        amounts = [by_month[m] for m in sorted(by_month)]
        usual = statistics.median(amounts[:-1])
        # The same charge every month, except that the newest may be a price change.
        if usual <= 0 or any(abs(a - usual) > usual * SUBSCRIPTION_SPREAD for a in amounts[:-1]):
            continue
        if abs(amounts[-1] - usual) > usual * SUBSCRIPTION_NEW_PRICE:
            continue
        latest = max(txns, key=lambda t: t.date)
        category = ledger.categories.get(latest.category_id) if latest.category_id else None
        found.append(
            {
                "merchant": latest.merchant,
                "category": category.name if category else None,
                "monthly_cents": amounts[-1],  # what it costs now
                "yearly_cents": amounts[-1] * 12,
                "last_date": latest.date.isoformat(),
                "months": len(by_month),
                # The newest charge is over the one before it: a price rise worth a look.
                "went_up": amounts[-1] - amounts[-2] >= 100 and amounts[-1] > amounts[-2] * 1.02,
            }
        )
    return sorted(found, key=lambda s: -s["monthly_cents"])


def rising(ledger: budget.Ledger, months: list[date]) -> list[dict]:
    if len(months) < RISING_BASE_MONTHS + 1:
        return []
    last, base = months[-1], months[-1 - RISING_BASE_MONTHS : -1]
    out = []
    for c in ledger.categories.values():
        if c.kind not in budget.BUDGETED:
            continue
        now = ledger.spent.get((c.id, last), 0)
        usual = statistics.median([max(ledger.spent.get((c.id, m), 0), 0) for m in base])
        if now - usual >= RISING_CENTS and now >= usual * (1 + RISING_SHARE):
            out.append(
                {
                    "category_id": c.id,
                    "category": c.name,
                    "month": last.isoformat(),
                    "spent_cents": now,
                    "usual_cents": round(usual),
                    "change_cents": round(now - usual),
                }
            )
    return sorted(out, key=lambda r: -r["change_cents"])


def ideas(db: DbSession, on: date) -> dict:
    accounts = budget.counted_accounts(db)
    months = budget.full_months(db, [a.id for a in accounts], on)
    first = budget.add_months(budget.month_start(on), -(IDEA_MONTHS - 1))
    ledger = budget.Ledger(db, accounts, min([first, *months]))
    subs = subscriptions(ledger, on)
    return {
        "subscriptions": subs,
        "subscriptions_yearly_cents": sum(s["yearly_cents"] for s in subs),
        "rising": rising(ledger, months),
    }
