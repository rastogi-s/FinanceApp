"""The Plan page: where today's savings lead, when work becomes optional, and ideas for saving more.

Projection. Today's net worth grows each month at a yearly return after inflation, plus the monthly savings,
for 30 years, in three bands because no one knows future returns. Every starting number comes from the
household's own data, says where it came from, and can be typed over:
- monthly savings: from the true savings rate (budget.py) in months with pay stubs, else what lands in the
  accounts minus spending plus the 401(k)/HSA the stubs say is put away, else the stubs alone (derive_savings);
- the middle return: from today's mix of stocks, bonds and cash (funds by their SEC filing), two points either
  side for the low and high bands (derive_returns);
- spending later: lean = bills and essentials, regular = the median month, fat = regular x 1.5, all from real
  transactions (Spending).

Financial independence. The number to reach is a year of spending divided by the withdrawal rate (4% by
default: $60k a year needs $1.5M).

Ideas. Subscriptions are merchants charged about the same amount in at least three of the last few months.
Utilities (power, gas, water, trash, the city, phone and internet) and other bills (rent, insurance, loans) recur
too, but they aren't something to cancel: they're listed on their own, flagged when the price goes up. A rising
category is one whose last full month came in well over its usual month. All of it is plain arithmetic on the
household's own transactions; no AI and nothing leaves the server.

Everything here is computed again on every request; the only thing stored is the household's choices
(the "plan" preference).
"""

import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, transfers
from financeapp.categorize import normalize_merchant
from financeapp.models import Category, Fund, Preference, Transaction

RETURNS = {"low": 4.0, "mid": 6.0, "high": 8.0}  # % a year after inflation, when there's no mix to go on
EXPECTED = {"stocks": 6.5, "bonds": 2.0, "cash": 0.5, "other": 5.0}  # long-run guesses, % a year after inflation
FUND_STOCKS = 0.9  # a fund with no filing looked up yet counts as this much stocks
SPREAD = 2.0  # the low and high bands sit this far either side of the middle
MIN_RETURN, MAX_RETURN = 1.0, 8.0
DEFAULT_RETURN_NOTE = "A common long-run range for a mostly-stocks portfolio, after inflation."
RECENT_DAYS = 90
YEARS = 30
MAX_YEARS = 60  # looking for the year the target is reached stops here
LEVELS = ("lean", "regular", "fat")
FAT = 1.5
SAVINGS_MONTHS = 6
DEFAULTS: dict = {
    "withdrawal_rate": 4.0,
    "level": "regular",
    "birth_year": None,
    "savings_cents": None,
    "return_mid": None,
    "spending": {},
}

IDEA_MONTHS = 5  # how far back subscriptions are looked for (the current month plus four full ones)
SUBSCRIPTION_MIN_MONTHS = 3
SUBSCRIPTION_SPREAD = 0.15  # each charge within 15% of the usual one
SUBSCRIPTION_NEW_PRICE = 0.50  # the newest charge may have gone up or down this much
HIGHER_SHARE = 0.15  # a utility that varies month to month: 15% over its usual...
HIGHER_CENTS = 10_00  # ...and at least $10 more
UTILITY_CATEGORIES = ("Utilities", "Phone & internet")
BILL_CATEGORIES = ("Rent & mortgage", "Insurance", "Loan payments", "Childcare & education")
UTILITY = re.compile(
    r"\benergy\b|electric|\bpower\b|natural gas|\bgas co|water|sewer|trash|\bwaste\b|sanitation|utilit|"
    r"\b(city|town|county|village) of\b|municipal|comcast|xfinity|verizon|at&t|t-mobile|spectrum|cox comm|"
    r"google fi|mint mobile|frontier comm|centurylink|optimum",
    re.IGNORECASE,
)
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


def bands(start: int, monthly: int, year: int, returns: dict, years: int = YEARS) -> list[dict]:
    return [
        {"year": year + y, **{k: grow(start, monthly, rate, y * 12) for k, rate in returns.items()}}
        for y in range(years + 1)
    ]


def target_for(yearly_spending: int, withdrawal_rate: float) -> int:
    return round(yearly_spending / (withdrawal_rate / 100))


# ---- the household's own numbers: each default says where it came from


def month_label(m: date) -> str:
    return m.strftime("%b %Y")


def span(months: list[date]) -> str:
    if len(months) == 1:
        return month_label(months[0])
    return f"{month_label(months[0])} to {month_label(months[-1])}"


def dollars(cents: int) -> str:
    return f"${round(cents / 100):,}"


class Spending:
    """What the household spends in a typical month: the median full month, or the last 90 days as a month."""

    def __init__(self, ledger: budget.Ledger, months: list[date], on: date, rows: list[dict] | None = None) -> None:
        self.months = months
        essential = {c.id for c in ledger.categories.values() if c.essential}
        if months:
            # The month rows leave out pay that landed uncategorized, which the ledger would take for a refund.
            per_month = (
                [r["spending_cents"] for r in rows]
                if rows
                else [sum(v for (_, m), v in ledger.spent.items() if m == month) for month in months]
            )
            lean = [
                sum(v for (cid, m), v in ledger.spent.items() if m == month and cid in essential) for month in months
            ]
            self.monthly = round(statistics.median(per_month))
            self.essentials = round(statistics.median(lean))
            self.where = f"your median month of spending, {span(months)}"
        else:
            since = on - timedelta(days=RECENT_DAYS)
            recent = [t for t in ledger.txns if t.date > since and ledger.is_spending(t)]
            self.monthly = round(-sum(t.amount_cents for t in recent) * 30 / RECENT_DAYS)
            self.essentials = round(
                -sum(t.amount_cents for t in recent if t.category_id in essential) * 30 / RECENT_DAYS
            )
            self.where = "your spending in the last 90 days, as a month" if recent else "no spending seen yet"


def spending_levels(spending: Spending, overrides: dict) -> dict:
    """Yearly spending at each level, where each came from, and whether someone typed it in instead."""
    made = {
        "lean": (
            spending.essentials * 12,
            f"Bills and essentials (rent, groceries, utilities and the like) from {spending.where}.",
        ),
        "regular": (spending.monthly * 12, f"From {spending.where}."),
        "fat": (round(spending.monthly * 12 * FAT), f"Regular spending and half again, from {spending.where}."),
    }
    out = {}
    for level in LEVELS:
        typed = overrides.get(level)
        cents, note = made[level]
        out[level] = {
            "yearly_cents": typed if typed is not None else cents,
            "typed": typed is not None,
            "derived_cents": cents,
            "note": note,
        }
    return out


PAY_NAMES = {
    "weekly": "every week",
    "biweekly": "every two weeks",
    "semimonthly": "twice a month",
    "monthly": "monthly",
}
STUBS_PER_PERSON = 6  # recent stubs per person to take the typical paycheck from


def from_pay_stubs(state: dict, include_match: bool) -> dict | None:
    """Take-home pay and what's put away before pay, in an average month, from each person's typical paycheck.

    The typical paycheck is the median of a person's recent stubs on their latest schedule, so one bonus or RSU
    stub doesn't move it. An average month on a two-weekly schedule is 26 / 12 paychecks, a little over two.
    """
    by_person: dict = {}
    for p in state["paychecks"]:  # newest first
        if not p["frequency"]:
            continue
        mine = by_person.setdefault(p["user_id"], [])
        if len(mine) < STUBS_PER_PERSON and (not mine or mine[0]["frequency"] == p["frequency"]):
            mine.append(p)
    if not by_person:
        return None
    net = put_away = 0
    paychecks = []
    for stubs in by_person.values():
        per_month = budget.PER_YEAR[stubs[0]["frequency"]] / 12
        typical_net = statistics.median(p["net_cents"] for p in stubs)
        typical_put = statistics.median(
            p["savings_cents"] + (p["employer_match_cents"] if include_match else 0) for p in stubs
        )
        net += round(typical_net * per_month)
        put_away += round(typical_put * per_month)
        paychecks.append(
            f"{dollars(round(typical_net))} a paycheck, paid {PAY_NAMES[stubs[0]['frequency']]} "
            f"({budget.PER_YEAR[stubs[0]['frequency']]} a year)"
        )
    return {"net": net, "put_away": put_away, "paychecks": paychecks}


def line(label: str, cents: float) -> dict:
    return {"label": label, "cents": round(cents)}


def month_row(ledger: budget.Ledger, m: date, payers: budget.Payroll, moved: int) -> dict:
    """One month of money in and out (budget.flows, the same sums as the savings rate on Budget). `moved` is how much
    came in from the household's own accounts, shown so it's clear it wasn't counted."""
    f = budget.flows(ledger, m, payers)
    return {
        "month": m.isoformat(),
        "pay_cents": f["pay"],
        "pay_deposits": f["pay_deposits"],
        "other_in_cents": sum(f["other"].values()),
        "taxes_cents": f["taxes"],
        "spending_cents": sum(f["spent"].values()),
        "moved_cents": moved,
    }


def pay_merchants(ledger: budget.Ledger, paychecks: list[dict], prefs: dict) -> budget.Payroll:
    stubs = [(date.fromisoformat(p["date"]), p["net_cents"]) for p in paychecks]
    return budget.pay_merchants(ledger, stubs, prefs, budget.latest_splits(paychecks))


MONEY_IN_ROWS = 12


def money_in(ledger: budget.Ledger, months: list[date], payers: budget.Payroll, names: set[str]) -> list[dict]:
    """Who the money coming in was from, over the months averaged, and how each is counted: pay, other money in, or
    the household's own money (a Zelle from their own name). Each can be switched, so the numbers can be fixed."""
    if not months:
        return []
    start, end = months[0], budget.add_months(months[-1], 1)
    groups: dict[tuple[str, str], dict] = {}
    for t in ledger.txns:
        if not (start <= t.date < end) or t.amount_cents <= 0 or ledger.is_taxes(t):
            continue
        sender = transfers.sender_of(t)
        if budget.is_pay_deposit(ledger, t, payers):
            kind, key = "pay", payers.source(t)
        elif sender is not None and transfers.is_own(t, names):
            kind, key = "me", sender
        elif ledger.is_income(t) or (ledger.kind(t) == "other" and sender is not None):
            kind, key = "other", sender or budget.employer_key(budget.text_of(t)) or budget.payer(t)
        else:
            continue
        g = groups.setdefault(
            (kind, key), {"key": key, "as": kind, "sender": sender is not None, "cents": 0, "count": 0, "days": set()}
        )
        g["cents"] += t.amount_cents
        day = payers.payday_of(t)[1] if kind == "pay" else t.date
        if kind != "pay" or day not in g["days"]:  # a paycheck split across accounts is one paycheck
            g["count"] += 1
        g["days"].add(day)
    rows = sorted(groups.values(), key=lambda g: -g["cents"])[:MONEY_IN_ROWS]
    for g in rows:
        del g["days"]
        g["monthly_cents"] = round(g["cents"] / len(months))
    return rows


def moved_in(db: DbSession, accounts: list, months: list[date]) -> dict[date, int]:
    """Money arriving from the household's own accounts each month (both sides of a transfer, or a card payment)."""
    if not months or not accounts:
        return {}
    transfer_ids = [c.id for c in db.scalars(select(Category).where(Category.kind == "transfer"))]
    rows = db.scalars(
        select(Transaction).where(
            Transaction.account_id.in_([a.id for a in accounts]),
            Transaction.date >= months[0],
            Transaction.date < budget.add_months(months[-1], 1),
            Transaction.amount_cents > 0,
            (Transaction.transfer_group_id.is_not(None)) | (Transaction.category_id.in_(transfer_ids)),
        )
    )
    out: dict[date, int] = defaultdict(int)
    for t in rows:
        out[budget.month_start(t.date)] += t.amount_cents
    return out


def derive_savings(
    state: dict,
    ledger: budget.Ledger,
    spending: Spending,
    on: date,
    rows: list[dict],
    employers: set[str] = frozenset(),
) -> dict:
    """The monthly savings to project, from the best evidence there is, a sentence on where it came from, and the
    sum it adds up from (breakdown lines that add up to the total).

    1. Months whose pay stubs are in (the true savings rate: gross pay - taxes - deductions - spending).
    2. Full months of what actually landed in your accounts minus spending, plus what your pay stubs say goes into
       a 401(k), HSA or ESPP before pay (it never reaches the bank, so deposits alone miss it).
    3. Pay stubs alone, when there's no full month of deposits yet: the typical paycheck as an average month,
       minus a typical month of spending.
    4. The last 90 days: money in minus money spent.
    """
    history = state["savings_history"][-SAVINGS_MONTHS:]
    gross = [m for m in history if m["basis"] == "gross" and m["stubs"]]  # stubs in, not estimated
    match = "401(k) match" if state["include_match"] else ""

    def avg(rows: list[dict], key: str) -> int:
        return round(sum(m[key] for m in rows) / len(rows))

    if gross:
        months = [date.fromisoformat(m["month"]) for m in gross]
        breakdown = [
            line(
                "Pay before taxes and other income" + (", with your employer's " + match if match else ""),
                avg(gross, "income_cents"),
            ),
            line("Taxes", -avg(gross, "taxes_cents")),
            line("Other paycheck deductions (health insurance and the like)", -avg(gross, "deductions_cents")),
            line("Spending", -avg(gross, "spending_cents")),
        ]
        return {
            "cents": avg(gross, "saved_cents"),
            "kind": "savings_rate",
            "short": "from your pay stubs and spending",
            "note": f"Average of {span(months)}, the months with pay stubs in.",
            "breakdown": [b for b in breakdown if b["cents"] or b["label"] == "Spending"],
        }
    stubs = from_pay_stubs(state, state["include_match"])
    if any(r["pay_cents"] or r["other_in_cents"] for r in rows):  # spending alone says nothing about saving
        months = [date.fromisoformat(r["month"]) for r in rows]
        put_away = stubs["put_away"] if stubs else 0
        per_month = round(sum(r["pay_deposits"] for r in rows) / len(rows), 1)
        who = sorted(employers)
        pay = line("Pay landing in your accounts", avg(rows, "pay_cents"))
        pay["detail"] = (
            f"{per_month:g} paycheck{'' if per_month == 1 else 's'} a month"
            + (f" from {', '.join(w.upper() for w in who[:3])}" if who else "")
            if per_month
            else "No paychecks found. Mark who pays you below."
        )
        breakdown = [pay]
        other = avg(rows, "other_in_cents")
        if other:
            breakdown.append(line("Other money in (interest, refunds, money from other people)", other))
        if put_away:
            breakdown.append(line("Into your 401(k), HSA or ESPP before pay, from your pay stubs", put_away))
        taxes = avg(rows, "taxes_cents")
        if taxes:
            breakdown.append(line("Taxes paid from your accounts", -taxes))
        breakdown.append(line("Spending", -avg(rows, "spending_cents")))
        return {
            "cents": sum(b["cents"] for b in breakdown),
            "kind": "take_home",
            "short": "what lands, plus your 401(k) and HSA" if put_away else "what lands minus spending",
            "note": f"Average month, {span(months)}. Money moved between your own accounts and card payments "
            "aren't counted as money in or as spending."
            + ("" if stubs else " Add a pay stub to count your 401(k) and HSA too, which never reach the bank."),
            "breakdown": breakdown,
            "excluded": line("Moved between your own accounts, not counted", avg(rows, "moved_cents")),
            "months": rows,
        }
    if stubs and spending.monthly:
        breakdown = [line("Take-home pay, from your pay stubs", stubs["net"])]
        if stubs["put_away"]:
            breakdown.append(line("Into your 401(k), HSA or ESPP before pay", stubs["put_away"]))
        breakdown.append(line("Spending", -spending.monthly))
        return {
            "cents": stubs["net"] + stubs["put_away"] - spending.monthly,
            "kind": "pay_stubs",
            "short": "from your pay stubs",
            "note": f"An average month from your pay stubs: {'; '.join(stubs['paychecks'])}. "
            f"Spending is {spending.where}. Once a full month of pay lands in your accounts, that's used instead.",
            "breakdown": breakdown,
        }
    since = on - timedelta(days=RECENT_DAYS)
    money_in = sum(t.amount_cents for t in ledger.txns if t.date > since and ledger.is_income(t))
    if money_in > 0:
        money_out = -sum(t.amount_cents for t in ledger.txns if t.date > since and ledger.is_spending(t))
        return {
            "cents": round((money_in - money_out) * 30 / RECENT_DAYS),
            "kind": "recent",
            "short": "the last 90 days",
            "note": "The last 90 days as a month: no full month of transactions yet.",
            "breakdown": [
                line("Money landing in your accounts", money_in * 30 / RECENT_DAYS),
                line("Spending", -money_out * 30 / RECENT_DAYS),
            ],
        }
    return {"cents": None, "kind": "none", "short": "", "note": "No pay or income seen yet.", "breakdown": []}


def derive_returns(db: DbSession, holdings: list[dict], allocation: list[dict]) -> dict:
    """A middle return from the household's own mix of stocks, bonds and cash, with two points either side.

    Funds count as stocks in the share their SEC filing says (look-through), or 90% stocks when there's no filing
    yet (most funds people hold are stock index funds).
    """
    weights = {k: 0 for k in EXPECTED}
    funds = {f.symbol: f for f in db.scalars(select(Fund))} if holdings else {}
    held = 0
    for h in holdings:
        cls = h["asset_class"]
        v = max(h["value_cents"], 0)
        held += v
        if cls == "Cash":
            weights["cash"] += v
        elif cls == "Bonds":
            weights["bonds"] += v
        elif cls == "Funds":
            f = funds.get(h["symbol"])
            share = float(f.equity_weight) if f is not None and f.equity_weight is not None else FUND_STOCKS
            weights["stocks"] += round(v * share)
            weights["bonds"] += v - round(v * share)
        else:
            weights["stocks"] += v
    for row in allocation:
        if row["asset_class"] == "Other investments":
            weights["other"] += max(row["cents"], 0)
        elif row["asset_class"] == "Cash":
            weights["cash"] += max(row["cents"], 0) - sum(
                max(h["value_cents"], 0) for h in holdings if h["asset_class"] == "Cash"
            )
    total = sum(weights.values())
    if total <= 0:
        return {**RETURNS, "typed": False, "derived_mid": RETURNS["mid"], "note": DEFAULT_RETURN_NOTE, "mix": None}
    mix = {k: v / total for k, v in weights.items()}
    mid = sum(EXPECTED[k] * share for k, share in mix.items())
    mid = min(max(round(mid * 2) / 2, MIN_RETURN), MAX_RETURN)
    pct = {k: round(share * 100) for k, share in mix.items()}
    parts = [f"{pct['stocks']}% stocks"] + [
        f"{pct[k]}% {name}" for k, name in (("bonds", "bonds"), ("cash", "cash"), ("other", "other")) if pct[k]
    ]
    return {
        "low": max(mid - SPREAD, 0.0),
        "mid": mid,
        "high": mid + SPREAD,
        "typed": False,
        "derived_mid": mid,
        "note": f"From your mix today: {', '.join(parts)}. Long-run guesses after inflation: stocks 6.5%, bonds 2%, "
        "cash 0.5% a year.",
        "mix": pct,
    }


def inputs(db: DbSession, state: dict, net_worth: int, on: date, holdings: list[dict], allocation: list[dict]) -> dict:
    """What the projection and the what-if sliders start from, each with a note on where it came from."""
    prefs = preferences(db)
    accounts = budget.counted_accounts(db)
    months = budget.full_months(db, [a.id for a in accounts], on)
    first = min([*months, on - timedelta(days=RECENT_DAYS)])
    ledger = budget.Ledger(db, accounts, first)
    payers = pay_merchants(ledger, state["paychecks"], budget.preferences(db))
    moved = moved_in(db, accounts, months)
    rows = [month_row(ledger, m, payers, moved.get(m, 0)) for m in months]
    spending = Spending(ledger, months, on, rows)
    measured = derive_savings(state, ledger, spending, on, rows, set(payers.counts))  # employers paid
    returns = derive_returns(db, holdings, allocation)
    typed_return = prefs.get("return_mid")
    if typed_return is not None:
        returns = {
            **returns,
            "low": max(typed_return - SPREAD, 0.0),
            "mid": typed_return,
            "high": typed_return + SPREAD,
            "typed": True,
        }
    typed = prefs["savings_cents"]
    return {
        "start_cents": net_worth,
        "year": on.year,
        "monthly_savings_cents": typed if typed is not None else measured["cents"],
        "measured_savings_cents": measured["cents"],
        "savings_source": {
            **{k: measured[k] for k in ("kind", "short", "note", "breakdown")},
            "excluded": measured.get("excluded"),
            "months": measured.get("months", []),
            "money_in": money_in(ledger, months, payers, transfers.own_names(db)),
        },
        "savings_typed": typed is not None,
        "returns": {k: returns[k] for k in ("low", "mid", "high")},
        "returns_source": {
            "typed": returns["typed"],
            "derived_mid": returns["derived_mid"],
            "note": returns["note"],
            "mix": returns["mix"],
        },
        "withdrawal_rate": prefs["withdrawal_rate"],
        "level": prefs["level"],
        "birth_year": prefs["birth_year"],
        "levels": spending_levels(spending, prefs["spending"]),
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
        "bands": bands(i["start_cents"], monthly, i["year"], i["returns"]),
        "target_cents": target,
        "fire_year": fire,
    }


# ---- ideas


def recurring_kind(merchant: str, category: Category | None) -> str:
    """ "utility", "bill" or "subscription": only a subscription is something to think about cancelling."""
    name = category.name if category else None
    if name in UTILITY_CATEGORIES or UTILITY.search(merchant):
        return "utility"
    if name in BILL_CATEGORIES:
        return "bill"
    return "subscription"


def recurring(ledger: budget.Ledger, on: date) -> tuple[list[dict], list[dict]]:
    """Subscriptions, and utilities and other bills, from the last few months of charges.

    A subscription is charged once a month, about the same each time. A utility or bill only has to come every
    month or so (a power bill moves with the weather, and a city can bill water and trash separately).
    """
    first = budget.add_months(budget.month_start(on), -(IDEA_MONTHS - 1))
    charges: dict[str, list] = defaultdict(list)
    for t in ledger.txns:
        if t.date >= first and t.amount_cents < 0 and not t.pending and ledger.is_spending(t):
            charges[normalize_merchant(t.merchant)].append(t)
    subs, bills = [], []
    for key, txns in charges.items():
        by_month: dict[date, int] = defaultdict(int)
        for t in txns:
            by_month[budget.month_start(t.date)] -= t.amount_cents
        if len(by_month) < SUBSCRIPTION_MIN_MONTHS or not key:
            continue
        latest = max(txns, key=lambda t: t.date)
        category = ledger.categories.get(latest.category_id) if latest.category_id else None
        kind = recurring_kind(latest.merchant, category)
        amounts = [by_month[m] for m in sorted(by_month)]
        usual = statistics.median(amounts[:-1])
        steady = usual > 0 and all(abs(a - usual) <= usual * SUBSCRIPTION_SPREAD for a in amounts[:-1])
        # The newest charge is over the one before it: a price rise worth a look.
        went_up = steady and amounts[-1] - amounts[-2] >= 100 and amounts[-1] > amounts[-2] * 1.02
        found = {
            "merchant": latest.merchant,
            "category": category.name if category else None,
            "monthly_cents": amounts[-1],  # what it costs now
            "yearly_cents": amounts[-1] * 12,
            "last_date": latest.date.isoformat(),
            "months": len(by_month),
            "went_up": went_up,
        }
        if kind != "subscription":
            # A bill that moves around (power in summer) is "higher than usual", not a new price.
            higher = (
                not steady
                and usual > 0
                and amounts[-1] - usual >= HIGHER_CENTS
                and amounts[-1] >= usual * (1 + HIGHER_SHARE)
            )
            bills.append({**found, "kind": kind, "usual_cents": round(usual), "higher": higher})
            continue
        # One charge a month, every month it shows up: a shop visited weekly isn't a subscription. The same charge
        # every month, except that the newest may be a price change.
        if len(txns) != len(by_month) or not steady or abs(amounts[-1] - usual) > usual * SUBSCRIPTION_NEW_PRICE:
            continue
        subs.append(found)
    return sorted(subs, key=lambda s: -s["monthly_cents"]), sorted(bills, key=lambda s: -s["monthly_cents"])


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
    subs, bills = recurring(ledger, on)
    return {
        "subscriptions": subs,
        "subscriptions_yearly_cents": sum(s["yearly_cents"] for s in subs),
        "utilities": bills,
        "rising": rising(ledger, months),
    }
