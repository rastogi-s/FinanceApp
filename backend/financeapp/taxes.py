"""The Taxes page: a rough, open estimate of this year's federal and state income tax, Social Security and Medicare,
against what the pay stubs say was already withheld, with the balance due or refund, a W-4 hint and estimated
payments. It is an estimate to plan with, not tax advice.

Every money number says where it came from and can be typed over; the household's answers (filing status, state,
deductions, children) and typed numbers are the "tax" preference. The tax tables are data/tax/<year>.json, with
their sources.

Pay. Each person's latest stub this year gives the year to date (its year-to-date column, or else a typical
paycheck times the paydays so far), and a typical paycheck (the middle of the last three) repeats for each payday
left on their schedule. Social Security and Medicare on the paychecks still to come follow the rates, so the wage
base and the extra 0.9% Medicare over $200,000 land on the right paycheck. A stub that only gave its total taxes is
split by the rates: Social Security 6.2%, Medicare 1.45%, state at the state's rate, and federal the rest.

Investments. Taxable interest, dividends (qualified or not) and fund capital gain distributions come from the
Dividends & interest page (income.tax_summary): this year so far, plus what came between today's date and the end of
last year as the guess for the rest of this year. Brokerages don't send sales, so realized gains are typed in; the
page shows the unrealized gain in taxable accounts to think about.

Not covered: AMT, self-employment tax, the refundable part of the child tax credit, other credits beyond the one
typed in, deduction phase-outs, loss carryovers, local taxes and a Social Security refund from two employers.
"""

import json
import math
import re
from datetime import date, timedelta
from functools import cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp.budget import PER_YEAR, add_months, month_start, next_payday
from financeapp.config import PACKAGE_DIR
from financeapp.models import Account, HoldingSnapshot, Paycheck, Preference, User

TABLES = PACKAGE_DIR / "data" / "tax"
STATUSES = {
    "single": "Single",
    "mfj": "Married filing jointly",
    "mfs": "Married filing separately",
    "hoh": "Head of household",
}
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip
NO_WAGE_TAX = {"AK", "FL", "NV", "NH", "SD", "TN", "TX", "WA", "WY"}  # no tax on wages (WA taxes some capital gains)
TAX_ADVANTAGED = re.compile(
    r"401|403|457|\bira\b|roth|\bhsa\b|health\s+savings|529|pension|retire|keogh|non-taxable|education|annuity", re.I
)
MAX_SCALE = 4.0  # this year so far is scaled to a whole year by at most this much (early January is too noisy)

DEFAULTS: dict[str, Any] = {
    "filing_status": None,  # asked first; the estimate uses "single" until it's answered
    "state": "NC",
    "state_rate": None,  # percent, for a state without a table here
    "deduction": "standard",  # standard | itemized (the "itemized" amount)
    "state_deduction": "standard",  # standard | itemized (the "state_itemized" amount)
    "children": 0,  # under 17 at the end of the year
    "other_dependents": 0,
    "qualified_share": 90,  # % of dividends that are qualified, until a 1099-DIV says
    "hsa_coverage": None,  # self | family | none: which yearly HSA limit applies (self until answered)
    "amounts": {},  # typed numbers by item key, in cents
}

# Every money number on the page: (key, label, may be negative). Derived ones have a note saying where they came
# from; the rest start at zero and are typed in.
ITEMS: dict[str, tuple[str, bool]] = {
    "wages": ("Pay", False),
    "retirement": ("Pre-tax 401(k) and 403(b)", False),
    "hsa_benefits": ("HSA and pre-tax health premiums", False),
    "stock_to_come": ("Stock pay still to vest this year", False),
    "spouse_wages": ("Spouse's pay", False),
    "spouse_pretax": ("Spouse's pre-tax 401(k) and HSA", False),
    "interest": ("Interest", False),
    "dividends": ("Dividends", False),
    "qualified_dividends": ("Of which qualified", False),
    "st_gains": ("Short-term gains (held a year or less)", True),
    "lt_gains": ("Long-term gains (held over a year)", True),
    "other_income": ("Other income", True),
    "adjustments": ("Adjustments (IRA deduction, student loan interest)", False),
    "hsa_outside": ("HSA money you put in yourself, outside payroll", False),
    "itemized": ("Itemized deductions", False),
    "state_itemized": ("State itemized deductions", False),
    "other_credits": ("Other credits", False),
    "federal_withheld": ("Federal tax withheld", False),
    "state_withheld": ("State tax withheld", False),
    "social_security_withheld": ("Social Security withheld", False),
    "medicare_withheld": ("Medicare withheld", False),
    "spouse_federal_withheld": ("Spouse's federal tax withheld", False),
    "spouse_state_withheld": ("Spouse's state tax withheld", False),
    "federal_estimated": ("Federal estimated payments made", False),
    "state_estimated": ("State estimated payments made", False),
    "last_year_tax": ("Last year's total federal tax", False),
}
SPOUSE_ITEMS = ("spouse_wages", "spouse_pretax", "spouse_federal_withheld", "spouse_state_withheld")
KINDS = (
    "gross", "federal", "state", "social_security", "medicare", "retirement", "hsa_benefits", "stock",
    "hsa", "employer_hsa",  # your own HSA money taken from pay (inside hsa_benefits) and your employer's on top
)  # fmt: skip


# ---- tables


@cache
def years() -> tuple[int, ...]:
    return tuple(sorted(int(p.stem) for p in TABLES.glob("*.json") if p.stem.isdigit()))


@cache
def table(year: int) -> dict:
    return json.loads((TABLES / f"{year}.json").read_text())


def table_for(year: int) -> dict:
    """This year's table, or the nearest one there is (the page says so)."""
    have = years()
    best = max((y for y in have if y <= year), default=min(have))
    return table(best)


def dollars(value: float | None) -> int | None:
    return None if value is None else round(value * 100)


# ---- the household's answers


def profile(db: DbSession) -> dict:
    p = db.get(Preference, "tax")
    value = p.value if p else {}
    return {**DEFAULTS, **value, "amounts": {**value.get("amounts", {})}}


def set_profile(db: DbSession, changes: dict) -> dict:
    """Changes the answers; in `amounts`, None goes back to the number the app works out."""
    current = profile(db)
    amounts = {**current["amounts"], **changes.pop("amounts", {})}
    value = {**current, **changes, "amounts": {k: v for k, v in amounts.items() if v is not None and k in ITEMS}}
    db.merge(Preference(key="tax", value=value))
    db.flush()
    return value


# ---- pay


def previous_payday(d: date, frequency: str) -> date:
    if frequency == "weekly":
        return d - timedelta(days=7)
    if frequency == "biweekly":
        return d - timedelta(days=14)
    if frequency == "semimonthly":  # the 15th and the last day of the month
        return d.replace(day=15) if d.day > 15 else month_start(d) - timedelta(days=1)
    before = add_months(month_start(d), -1)
    last = month_start(d) - timedelta(days=1)
    return before.replace(day=min(d.day, last.day))


def split(p: Paycheck, state_rate: float, state_allowance: int) -> tuple[dict[str, int], bool]:
    """One paycheck by kind. A stub that only gave its total taxes is split by the rates (and says so)."""
    d = (p.detail or {}).get("this", {})
    gross = p.gross_cents
    hsa_benefits = d.get("hsa", 0) + d.get("benefits", 0)
    retirement = d.get("retirement")
    if retirement is None:
        # No detail: everything saved before pay counts as pre-tax 401(k), the usual case.
        retirement = 0 if any(k in d for k in ("roth", "hsa", "espp")) else p.savings_cents
    taxes = {k: d.get(k) for k in ("federal", "state", "social_security", "medicare")}
    estimated = any(v is None for v in taxes.values())
    if estimated:
        left = p.taxes_cents - sum(v for v in taxes.values() if v) - d.get("local", 0) - d.get("state_other", 0)
        fica = gross - hsa_benefits
        if taxes["social_security"] is None:
            taxes["social_security"] = min(round(fica * 0.062), max(left, 0))
            left -= taxes["social_security"]
        if taxes["medicare"] is None:
            taxes["medicare"] = min(round(fica * 0.0145), max(left, 0))
            left -= taxes["medicare"]
        if taxes["state"] is None and taxes["federal"] is None:
            guess = round((gross - retirement - hsa_benefits - state_allowance) * state_rate)
            taxes["state"] = min(max(guess, 0), max(left, 0))
            taxes["federal"] = max(left - taxes["state"], 0)
        elif taxes["state"] is None:
            taxes["state"] = max(left, 0)
        elif taxes["federal"] is None:
            taxes["federal"] = max(left, 0)
    out = {
        "gross": gross,
        "retirement": retirement,
        "hsa_benefits": hsa_benefits,
        "stock": d.get("stock", 0),
        "hsa": d.get("hsa", 0),
        "employer_hsa": d.get("employer_hsa", 0),
        **{k: v or 0 for k, v in taxes.items()},
    }
    return out, estimated


def project_pay(
    stubs: list[Paycheck],
    year: int,
    t: dict,
    state: tuple[float, int, float],
    extra_stock: int = 0,
) -> dict | None:
    """One person's pay for the year from their stubs (newest first): so far, still to come, and the whole year."""
    if not stubs:
        return None
    fed = t["federal"]
    state_rate, state_std, state_supplemental = state  # rate, standard deduction (dollars), supplemental rate
    latest = stubs[0]
    frequency = latest.frequency or next((s.frequency for s in stubs if s.frequency), None)
    per_year = PER_YEAR.get(frequency or "biweekly", 26)
    allowance = round(state_std * 100 / per_year)
    this_year = [s for s in stubs if s.date.year == year]
    recent = this_year[:3] or stubs[:1]
    splits = {s.id or i: split(s, state_rate, allowance) for i, s in enumerate(recent)}
    ranked = sorted(splits.values(), key=lambda sp: sp[0]["gross"] - sp[0]["stock"])
    typical = dict(ranked[len(ranked) // 2][0])
    estimated_split = any(sp[1] for sp in splits.values())
    # A typical paycheck is regular pay: stock income comes in lumps (typed in as "still to vest").
    typical["gross"] -= typical["stock"]
    typical["stock"] = 0

    ytd = dict.fromkeys(KINDS, 0)
    ytd_source = "none"
    if latest.date.year == year:
        own, _ = split(latest, state_rate, allowance)
        printed = (latest.detail or {}).get("ytd", {})
        if printed.get("gross"):
            ytd_source = "stub"
            ytd["gross"] = printed["gross"]
            scale = printed["gross"] / own["gross"] if own["gross"] else 0
            for k in KINDS[1:]:
                if k == "hsa_benefits" and ("hsa" in printed or "benefits" in printed):
                    ytd[k] = printed.get("hsa", 0) + printed.get("benefits", 0)
                elif k != "hsa_benefits" and k in printed:
                    ytd[k] = printed[k]
                else:
                    ytd[k] = round(own[k] * scale)
        else:
            ytd_source = "estimated"
            paydays = 1
            d = latest.date
            while (d := previous_payday(d, frequency or "biweekly")).year == year:
                paydays += 1
            for s in this_year:
                sp, _ = split(s, state_rate, allowance)
                for k in KINDS:
                    ytd[k] += sp[k]
            for k in KINDS:
                ytd[k] += max(paydays - len(this_year), 0) * typical[k]

    left = 0
    d = latest.date
    end = date(year, 12, 31)
    while (d := next_payday(d, frequency or "biweekly")) <= end:
        if d.year == year:
            left += 1

    # The paychecks still to come, one at a time, so Social Security stops at the wage base and the extra Medicare
    # starts over $200,000. Stock still to vest comes last, withheld at the supplemental rates.
    full = dict(ytd)
    fica_so_far = ytd["gross"] - ytd["hsa_benefits"]
    checks = [dict(typical) for _ in range(left)]
    if extra_stock:
        checks.append(
            {
                **dict.fromkeys(KINDS, 0),
                "gross": extra_stock,
                "stock": extra_stock,
                "federal": round(extra_stock * fed["supplemental_withholding_rate"]),
                "state": round(extra_stock * state_supplemental),
            }
        )
    ss_base = dollars(fed["social_security"]["wage_base"])
    med_line = dollars(fed["medicare"]["withholding_threshold"])
    for c in checks:
        wages = c["gross"] - c["hsa_benefits"]
        room = max(ss_base - fica_so_far, 0)
        c["social_security"] = round(min(wages, room) * fed["social_security"]["rate"])
        over = max(fica_so_far + wages - max(med_line, fica_so_far), 0)
        c["medicare"] = round(wages * fed["medicare"]["rate"] + over * fed["medicare"]["additional_rate"])
        fica_so_far += wages
        for k in KINDS:
            full[k] += c[k]
    return {
        "frequency": frequency,
        "frequency_known": frequency is not None,
        "latest": latest.date.isoformat(),
        "stubs_this_year": len(this_year),
        "paychecks_left": left,
        "ytd_source": ytd_source,
        "split_estimated": estimated_split,
        "typical": typical,
        "ytd": ytd,
        "full": full,
    }


# ---- what the database knows


def taxable_accounts(db: DbSession) -> list[Account]:
    from financeapp.household import counted

    return [a for a in counted(db) if not TAX_ADVANTAGED.search(f"{a.subtype or ''} {a.name}")]


def investment_income(db: DbSession, today: date) -> dict:
    """Taxable interest and dividends for the whole year, from the Dividends & interest page's payments."""
    from financeapp import household, income

    accounts = household.counted(db)
    pays = income.payments(db, accounts, min(income.since_for(today), date(today.year - 1, 1, 1)))
    classes = {h.symbol: h.asset_class for h in income.holdings(db, accounts)}
    return from_payments(pays, classes, income.history_start(db, accounts, today), today)


def from_payments(pays: list, classes: dict[str, str], history_from: date, today: date) -> dict:
    """This year so far (income.tax_summary), plus what came between today's date and the end of last year, which is
    the best guess for the rest of this year (December's fund distributions included). Without last year's history,
    this year so far is scaled to a whole year."""
    from financeapp import income

    year = today.year
    so_far = income.tax_summary(pays, year, today, classes, history_from)
    year_ago = date(year - 1, today.month, min(today.day, 28))
    rest = income.tax_summary([p for p in pays if p.date > year_ago], year - 1, today, classes, history_from)

    def amounts(t: dict) -> dict[str, int]:
        d = t["dividends"]
        return {
            "interest_cents": t["interest"]["total_cents"],
            "dividends_cents": d["total_cents"],
            "qualified_cents": d["estimated_qualified_cents"],
            "capital_gain_cents": d["capital_gain_distributions_cents"],
        }

    now, later = amounts(so_far), amounts(rest)
    out = {**now, "so_far": now, "through": so_far["through"], "estimated": so_far["estimated"] or rest["estimated"]}
    if not any(now.values()) and not any(later.values()):
        return out | {"basis": "none"}
    if history_from <= year_ago:
        return out | {k: now[k] + later[k] for k in now} | {"basis": "last_year", "rest": later}
    elapsed = (today - max(date(year, 1, 1), history_from)).days + 1
    scale = min(365 / max(elapsed, 1), MAX_SCALE)
    return out | {k: round(v * scale) for k, v in now.items()} | {"basis": "scaled"}


def unrealized_gain(db: DbSession) -> int | None:
    """Gain not yet sold in taxable brokerage accounts, from the latest holdings with a cost basis."""
    ids = [a.id for a in taxable_accounts(db) if a.type == "investment"]
    if not ids:
        return None
    latest = (
        select(HoldingSnapshot.account_id, func.max(HoldingSnapshot.date).label("d"))
        .where(HoldingSnapshot.account_id.in_(ids))
        .group_by(HoldingSnapshot.account_id)
        .subquery()
    )
    rows = db.execute(
        select(HoldingSnapshot.value_cents, HoldingSnapshot.cost_basis_cents).join(
            latest, (HoldingSnapshot.account_id == latest.c.account_id) & (HoldingSnapshot.date == latest.c.d)
        )
    ).all()
    with_basis = [(v, b) for v, b in rows if b is not None]
    return sum(v - b for v, b in with_basis) if with_basis else None


def facts(db: DbSession, viewer_id: int, today: date) -> dict:
    from financeapp import plan

    people = {u.id: u.name for u in db.scalars(select(User))}
    stubs = stubs_by_person(db)
    return {
        "viewer_id": viewer_id,
        "people": people,
        "stubs": stubs,
        "investments": investment_income(db, today),
        "unrealized_gain_cents": unrealized_gain(db),
        "birth_year": plan.preferences(db).get("birth_year"),
    }


# ---- the arithmetic


def bands(amount: int, brackets: list, start: int = 0) -> list[dict]:
    """`amount` stacked on top of `start`, across brackets [[up to (dollars, None = no top), rate], ...]."""
    rows = []
    low = 0
    for top, rate in brackets:
        high = math.inf if top is None else dollars(top)
        lo, hi = max(low, start), min(high, start + amount)
        if hi > lo:
            rows.append({"rate": rate, "from_cents": lo, "cents": int(hi - lo), "tax_cents": round((hi - lo) * rate)})
        low = high
    return rows


def lookup(rows: list, amount: int) -> int:
    for top, value in rows:
        if top is None or amount <= dollars(top):
            return value
    return 0


def state_rates(t: dict, prof: dict, status: str) -> tuple[float, float, float]:
    """The state's rate, standard deduction (dollars) and supplemental withholding rate."""
    state_code = prof["state"]
    state_table = t["states"].get(state_code)
    if state_table:
        rate = state_table["rate"]
        return rate, state_table["standard_deduction"][status], state_table.get("supplemental_withholding_rate", rate)
    rate = 0.0 if state_code in NO_WAGE_TAX else (prof["state_rate"] or 0) / 100
    return rate, 0, rate


def people_pay(t: dict, prof: dict, f: dict, year: int) -> tuple[dict, int | None]:
    """Each counted person's pay for the year (project_pay), and whose pay stock still to vest goes with. Everyone's
    counts on a joint return, otherwise the person looking (or the only person with stubs)."""
    fed = t["federal"]
    status = prof["filing_status"] or "single"
    state_rate, state_std, supplemental = state_rates(t, prof, status)
    typed = prof["amounts"]
    with_stubs = [uid for uid, s in f["stubs"].items() if s]
    if status == "mfj":
        counted = with_stubs
    elif f["viewer_id"] in with_stubs or len(with_stubs) != 1:
        counted = [f["viewer_id"]] if f["viewer_id"] in with_stubs else []
    else:
        counted = with_stubs
    primary = f["viewer_id"] if f["viewer_id"] in counted else (counted[0] if counted else None)
    stock_to_come = typed.get("stock_to_come", 0)
    pay = {}
    for uid in counted:
        pay[uid] = project_pay(
            f["stubs"][uid], year, t, (state_rate, state_std, supplemental), stock_to_come if uid == primary else 0
        )
    if primary is None and stock_to_come:
        pay[None] = {
            "full": {
                **dict.fromkeys(KINDS, 0),
                "gross": stock_to_come,
                "stock": stock_to_come,
                "federal": round(stock_to_come * fed["supplemental_withholding_rate"]),
                "state": round(stock_to_come * supplemental),
                "social_security": round(stock_to_come * fed["social_security"]["rate"]),
                "medicare": round(stock_to_come * fed["medicare"]["rate"]),
            },
            "paychecks_left": 0,
        }
    return pay, primary


def hsa_check(t: dict, prof: dict, birth_year: int | None, year: int, full: dict, outside: int, left: int) -> dict:
    """Your HSA money and your employer's share one yearly limit (self-only or family coverage, plus $1,000 from 55).
    Both come from the stubs for the whole year; money put in outside payroll is typed in."""
    limits = t["federal"]["limits"]
    coverage = prof.get("hsa_coverage")
    limit = dollars(limits["hsa_family" if coverage == "family" else "hsa_self"])
    catch_up = bool(birth_year and year - birth_year >= 55)
    if catch_up:
        limit += dollars(limits.get("hsa_catch_up", 1000))
    own, employer = full.get("hsa", 0), full.get("employer_hsa", 0)
    total = own + employer + outside
    room = limit - total
    return {
        "coverage": coverage,
        "has_hsa": total > 0 or coverage in ("self", "family"),
        "limit_cents": limit,
        "self_cents": dollars(limits["hsa_self"]),
        "family_cents": dollars(limits["hsa_family"]),
        "catch_up": catch_up,
        "own_cents": own,
        "employer_cents": employer,
        "outside_cents": outside,
        "total_cents": total,
        "room_cents": max(room, 0),
        "over_cents": max(-room, 0),
        "paychecks_left": left,
        "per_paycheck_cents": math.floor(room / left) if room > 0 and left else 0,
        "year": year,
    }


def estimate(t: dict, prof: dict, f: dict, today: date) -> dict:
    fed = t["federal"]
    year = today.year
    status = prof["filing_status"] or "single"
    joint = status == "mfj"
    state_code = prof["state"]
    state_table = t["states"].get(state_code)
    state_rate = state_rates(t, prof, status)[0]
    typed = prof["amounts"]
    stock_to_come = typed.get("stock_to_come", 0)
    pay, primary = people_pay(t, prof, f, year)
    full = {k: sum(p["full"][k] for p in pay.values()) for k in KINDS}
    hsa = hsa_check(
        t,
        prof,
        f.get("birth_year"),
        year,
        full,
        typed.get("hsa_outside", 0),
        pay[primary]["paychecks_left"] if primary in pay else 0,
    )
    # Over the shared limit: the outside money stops being deductible first, then your own payroll HSA money is
    # taxed again, then your employer's extra is taxed as pay.
    payroll_over = max(hsa["own_cents"] + hsa["employer_cents"] - hsa["limit_cents"], 0)
    own_over = min(payroll_over, hsa["own_cents"])
    employer_over = payroll_over - own_over
    outside_deductible = min(
        hsa["outside_cents"], max(hsa["limit_cents"] - hsa["own_cents"] - hsa["employer_cents"], 0)
    )

    # 401(k) deferrals stop at the yearly limit (plus the catch-up from 50).
    limit = dollars(fed["limits"]["401k"])
    if f.get("birth_year") and year - f["birth_year"] >= 50:
        limit += dollars(fed["limits"]["401k_catch_up"])
    retirement = min(full["retirement"], limit * max(len(pay), 1))

    inv = f["investments"]
    dividends = typed.get("dividends", inv["dividends_cents"])
    # Qualified: the share the payments show (labeled, or sorted by what the holding is), else the answer's share.
    qualified_ratio = (
        inv["qualified_cents"] / inv["dividends_cents"] if inv["dividends_cents"] else prof["qualified_share"] / 100
    )
    derived = {
        "wages": full["gross"],
        "retirement": retirement,
        "hsa_benefits": full["hsa_benefits"] - own_over,
        "interest": inv["interest_cents"],
        "dividends": inv["dividends_cents"],
        "qualified_dividends": round(dividends * qualified_ratio),
        "lt_gains": inv["capital_gain_cents"],
        "federal_withheld": full["federal"],
        "state_withheld": full["state"],
        "social_security_withheld": full["social_security"],
        "medicare_withheld": full["medicare"],
    }

    def v(key: str) -> int:
        if key in SPOUSE_ITEMS and not joint:
            return 0
        return typed.get(key, derived.get(key, 0))

    # Federal income
    st, lt = v("st_gains"), v("lt_gains")
    net = st + lt
    loss_limit = dollars(fed["capital_loss_limit"][status])
    if net < 0:
        gains_ordinary, gains_preferred = -min(-net, loss_limit), 0
    elif st >= 0 and lt >= 0:
        gains_ordinary, gains_preferred = st, lt
    elif st < 0:
        gains_ordinary, gains_preferred = 0, lt + st
    else:
        gains_ordinary, gains_preferred = st + lt, 0
    wages_taxed = (
        v("wages") - v("retirement") - v("hsa_benefits") + v("spouse_wages") - v("spouse_pretax") + employer_over
    )
    interest, dividends = v("interest"), v("dividends")
    qualified = min(v("qualified_dividends"), dividends)
    income = wages_taxed + interest + dividends + gains_ordinary + gains_preferred + v("other_income")
    agi = max(income - v("adjustments") - min(v("hsa_outside"), outside_deductible), 0)
    standard = dollars(fed["standard_deduction"][status])
    itemizing = prof["deduction"] == "itemized"
    deduction = v("itemized") if itemizing else standard
    taxable = max(agi - deduction, 0)
    preferred = min(taxable, qualified + gains_preferred)
    ordinary = taxable - preferred
    ordinary_rows = bands(ordinary, fed["brackets"][status])
    gains_rows = bands(preferred, fed["capital_gains"][status], start=ordinary)
    ordinary_tax = sum(r["tax_cents"] for r in ordinary_rows)
    gains_tax = sum(r["tax_cents"] for r in gains_rows)

    ctc = fed["child_tax_credit"]
    child_credit = (
        dollars(ctc["per_child"]) * prof["children"] + dollars(ctc["other_dependent"]) * prof["other_dependents"]
    )
    over = max(agi - dollars(ctc["phaseout_start"][status]), 0)
    child_credit = max(child_credit - math.ceil(over / 100_000) * dollars(ctc["phaseout_per_1000"]), 0)
    before_credits = ordinary_tax + gains_tax
    credits = min(child_credit + v("other_credits"), before_credits)
    income_tax = before_credits - credits

    niit_cfg = fed["niit"]
    investment = interest + dividends + max(net, 0)
    niit_base = min(investment, max(agi - dollars(niit_cfg["threshold"][status]), 0))
    niit = round(niit_base * niit_cfg["rate"])

    federal_total = income_tax + niit
    federal_paid = v("federal_withheld") + v("spouse_federal_withheld") + v("federal_estimated")
    federal_balance = federal_total - federal_paid
    marginal = ordinary_rows[-1]["rate"] if ordinary_rows else fed["brackets"][status][0][1]

    # Social Security and Medicare (the spouse's own are taken as exactly withheld)
    med = fed["medicare"]
    fica_wages = full["gross"] - full["hsa_benefits"]
    ss_due = sum(
        round(min(p["full"]["gross"] - p["full"]["hsa_benefits"], dollars(fed["social_security"]["wage_base"]))
              * fed["social_security"]["rate"])
        for p in pay.values()
    )  # fmt: skip
    medicare_wages = fica_wages + v("spouse_wages")
    additional_base = max(medicare_wages - dollars(med["additional_threshold"][status]), 0)
    additional = round(additional_base * med["additional_rate"])
    medicare_due = round(fica_wages * med["rate"]) + additional
    spouse_additional_withheld = round(
        max(v("spouse_wages") - dollars(med["withholding_threshold"]), 0) * med["additional_rate"]
    )
    fica_paid = v("social_security_withheld") + v("medicare_withheld") + spouse_additional_withheld
    # Only the extra 0.9% Medicare settles on the return (Form 8959). Too much Social Security or Medicare withheld by
    # one employer isn't refunded at filing (payroll fixes it), so the rest is a check, not part of the balance.
    additional_withheld = max(v("medicare_withheld") - round(fica_wages * med["rate"]), 0) + spouse_additional_withheld
    fica_balance = additional - additional_withheld
    fica_difference = ss_due + medicare_due - fica_paid

    # State
    state = {"code": state_code, "name": STATES.get(state_code, state_code), "supported": bool(state_table)}
    state_itemizing = prof["state_deduction"] == "itemized"
    if state_table:
        state_standard = dollars(state_table["standard_deduction"][status])
        state_deduction = v("state_itemized") if state_itemizing else state_standard
        child_deduction = 0
        if "child_deduction" in state_table:
            child_deduction = dollars(lookup(state_table["child_deduction"][status], agi)) * prof["children"]
        state_taxable = max(agi - state_deduction - child_deduction, 0)
        state_tax = round(state_taxable * state_rate)
        state |= {
            "rate": state_rate,
            "standard_cents": state_standard,
            "deduction_cents": state_deduction,
            "itemizing": state_itemizing,
            "child_deduction_cents": child_deduction,
            "taxable_cents": state_taxable,
            "sources": state_table["sources"],
            "notes": state_table.get("notes", []),
        }
    elif state_code in NO_WAGE_TAX:
        state_tax = 0
        state |= {"rate": 0, "no_wage_tax": True}
    else:
        state_deduction = v("state_itemized")
        state_taxable = max(agi - state_deduction, 0)
        state_tax = round(state_taxable * state_rate)
        state |= {
            "rate": state_rate,
            "rate_typed": prof["state_rate"] is not None,
            "deduction_cents": state_deduction,
            "taxable_cents": state_taxable,
        }
    state_paid = v("state_withheld") + v("spouse_state_withheld") + v("state_estimated")
    state |= {"tax_cents": state_tax, "paid_cents": state_paid, "balance_cents": state_tax - state_paid}

    # Hints: the W-4 and NC-4 for the paychecks left, and estimated payments.
    left = pay[primary]["paychecks_left"] if primary in pay else 0
    per_year = PER_YEAR.get((pay.get(primary) or {}).get("frequency") or "biweekly", 26)
    owed_federal = federal_balance + fica_balance
    hints = {"paychecks_left": left, "federal": None, "state": None, "quarterly": None}
    if left:
        if owed_federal > 0:
            hints["federal"] = {"kind": "more", "per_check_cents": math.ceil(owed_federal / left)}
        elif owed_federal < -(100 * left) and marginal > 0:
            per_check = -owed_federal // left
            hints["federal"] = {
                "kind": "less",
                "per_check_cents": per_check,
                "deductions_cents": round(per_check * per_year / marginal / 10_000) * 10_000,
            }
        if state["balance_cents"] > 0:
            hints["state"] = {"kind": "more", "per_check_cents": math.ceil(state["balance_cents"] / left)}
        elif state["balance_cents"] < -(100 * left):
            hints["state"] = {"kind": "less", "per_check_cents": -state["balance_cents"] // left}

    harbor = fed["safe_harbor"]
    liability = federal_total + additional
    paid_toward = federal_paid + max(v("medicare_withheld") - round(fica_wages * med["rate"]), 0)
    required = round(liability * harbor["current_year"])
    last_year = v("last_year_tax")
    if last_year:
        factor = (
            harbor["prior_year_high_income"]
            if agi > dollars(harbor["high_income_agi"][status])
            else harbor["prior_year"]
        )
        required = min(required, round(last_year * factor))
    shortfall = required - paid_toward
    due_dates = [d for d in fed["estimated_due_dates"] if date.fromisoformat(d) >= today]
    floor = dollars(fed["penalty_floor"])
    state_floor = dollars(state_table["penalty_floor"]) if state_table else floor
    if (liability - paid_toward >= floor and shortfall > 0) or state["balance_cents"] >= state_floor:
        hints["quarterly"] = {
            "federal_needed": liability - paid_toward >= floor and shortfall > 0,
            "federal_shortfall_cents": max(shortfall, 0),
            "required_cents": required,
            "uses_last_year": bool(last_year),
            "state_needed": state["balance_cents"] >= state_floor,
            "state_cents": max(state["balance_cents"], 0),
            "due_dates": due_dates,
            "per_date_cents": math.ceil(max(shortfall, 0) / len(due_dates)) if due_dates else None,
        }

    # Where each number came from
    people = f["people"]
    notes: dict[str, str] = {}
    pay_lines = []
    for uid, p in pay.items():
        if uid is None:
            continue
        name = people.get(uid) or "You"
        so_far = (
            f"{money(p['ytd']['gross'])} so far (the year-to-date on the {short(p['latest'])} stub)"
            if p["ytd_source"] == "stub"
            else f"about {money(p['ytd']['gross'])} so far (estimated from {p['stubs_this_year']} stub"
            f"{'s' if p['stubs_this_year'] != 1 else ''} this year)"
            if p["ytd_source"] == "estimated"
            else "no stub from this year yet"
        )
        more = (
            f"{p['paychecks_left']} more paycheck{'s' if p['paychecks_left'] != 1 else ''} of about "
            f"{money(p['typical']['gross'])}"
            + ("" if p["frequency_known"] else " (every 2 weeks assumed; set how often on a stub)")
        )
        pay_lines.append(
            f"{name}: {so_far}, plus {more}." if len(pay) > 1 else f"{so_far[0].upper()}{so_far[1:]}, plus {more}."
        )
    if not pay:
        notes["wages"] = "No pay stubs yet. Add one under Budget, Pay stubs, or type your pay for the year."
    else:
        notes["wages"] = " ".join(pay_lines)
        if stock_to_come:
            notes["wages"] += f" Includes {money(stock_to_come)} of stock still to vest."
    split_estimated = any(p.get("split_estimated") for p in pay.values())
    notes["retirement"] = (
        "From the stubs, for the whole year"
        + (f", capped at the {money(limit)} limit." if full["retirement"] > retirement else ".")
        + " Lowers federal and state tax, not Social Security or Medicare."
    )
    notes["hsa_benefits"] = (
        "From the stubs: HSA and health, dental and vision premiums taken before tax. Lowers every tax."
        + (f" Less {money(own_over)} of HSA money over the yearly limit, which is taxed." if own_over else "")
    )
    notes["hsa_outside"] = (
        "Money you put into your HSA yourself, not through payroll. Deductible up to what's left of the yearly limit "
        "after your payroll HSA money and your employer's."
    )
    notes["stock_to_come"] = (
        "RSU vests or ESPP sales the rest of the year. Withheld at the 22% federal supplemental rate"
        + (f" and {state_table['supplemental_withholding_rate'] * 100:.2f}% state" if state_table else "")
        + ". Stock already vested is in your pay."
    )
    notes["spouse_wages"] = "Pay from a job without stubs in the app. A spouse's stubs in the app are counted in Pay."
    if inv["basis"] == "none":
        notes["interest"] = "No interest seen in taxable accounts yet. Type it from your bank's year-end summary."
        notes["dividends"] = (
            "No dividends seen in taxable brokerage accounts yet. Type them from your broker if you have any."
        )
        notes["qualified_dividends"] = (
            f"{prof['qualified_share']}% of dividends, typical for US stock funds. Taxed at the lower long-term rates."
        )
    else:
        sf = inv["so_far"]
        how = (
            "plus what came between now and December last year"
            if inv["basis"] == "last_year"
            else "scaled to a whole year (there's no history from last year to go on)"
        )
        through = short(inv["through"])
        notes["interest"] = (
            f"{money(sf['interest_cents'])} so far in taxable bank and brokerage accounts (through {through}), {how}. "
            "From Dividends & interest; retirement and HSA accounts left out."
        )
        notes["dividends"] = (
            f"{money(sf['dividends_cents'])} so far in taxable brokerage accounts (through {through}), {how}. "
            "Ordinary dividends, box 1a of the 1099-DIV."
        )
        notes["qualified_dividends"] = (
            "As the brokerage labels them"
            + (", or by what each holding is where it doesn't (bond and money market funds are ordinary)"
               if inv["estimated"] else "")
            + ". Taxed at the lower long-term rates."
        )  # fmt: skip
    gain = f.get("unrealized_gain_cents")
    gains_note = "Brokerages don't send sales to the app. Type this from your broker's realized gain/loss page."
    if gain is not None:
        gains_note += f" Your taxable accounts hold {money(gain)} of gains not yet sold."
    notes["st_gains"] = gains_note + " Taxed like pay."
    notes["lt_gains"] = gains_note + " Taxed at 0%, 15% or 20%."
    if inv["basis"] != "none" and inv["capital_gain_cents"]:
        notes["lt_gains"] = (
            f"Fund capital gain distributions: {money(inv['capital_gain_cents'])} for the year, from Dividends & "
            "interest. Add gains from your own sales (your broker's realized gain/loss page). Taxed at 0%, 15% or 20%."
        )
    notes["other_income"] = "Side work, rental, gambling, a taxable state refund. A loss can go here as a negative."
    notes["adjustments"] = "Deductible IRA contributions, student loan interest, self-employed health insurance."
    notes["itemized"] = "Mortgage interest, state and local taxes (capped), charity, medical over 7.5% of income."
    notes["state_itemized"] = (
        "North Carolina allows mortgage interest and property tax up to $20,000 together, charity and medical."
        if state_code == "NC"
        else "Your state's own deductions."
    )
    notes["other_credits"] = "Education, child care, energy and other credits from your own forms."

    def withheld_note(kind: str) -> str:
        if not pay:
            return "No pay stubs yet."
        so_far = sum(p["ytd"][kind] for p in pay.values() if "ytd" in p)
        note = f"{money(so_far)} so far on the stubs, plus the same per paycheck for the rest of the year."
        if split_estimated:
            note += (
                " Split from each stub's total taxes by the usual rates; add the breakdown on a stub for exact numbers."
            )
        return note

    notes["federal_withheld"] = withheld_note("federal")
    notes["state_withheld"] = withheld_note("state")
    notes["social_security_withheld"] = (
        f"6.2% of pay up to the {money(dollars(fed['social_security']['wage_base']))} wage base."
    )
    notes["medicare_withheld"] = "1.45% of pay, and 0.9% more on pay over $200,000 with one employer."
    notes["federal_estimated"] = "Payments sent with Form 1040-ES this year."
    notes["state_estimated"] = "Payments sent to the state this year (NC-40 in North Carolina)."
    notes["last_year_tax"] = (
        "Line 24 of last year's 1040. With it, the safe harbor can use 100% of last year (110% over $150,000 AGI)."
    )

    items = []
    for key, (label, negative) in ITEMS.items():
        if key in SPOUSE_ITEMS and not joint:
            continue
        if (key == "itemized" and not itemizing) or (key == "state_itemized" and not state_itemizing and state_table):
            continue
        if key == "state_itemized" and (state_code in NO_WAGE_TAX):
            continue
        items.append(
            {
                "key": key,
                "label": label,
                "cents": v(key),
                "derived_cents": derived.get(key),
                "typed": key in typed,
                "negative_ok": negative,
                "note": notes.get(key, ""),
            }
        )

    return {
        "year": year,
        "table_year": t["year"],
        "table_checked": t.get("checked"),
        "profile": {k: prof[k] for k in DEFAULTS if k != "amounts"},
        "answered": prof["filing_status"] is not None,
        "statuses": STATUSES,
        "states": STATES,
        "states_with_tables": sorted(k for k in t["states"] if not k.startswith("_")),
        "no_wage_tax": sorted(NO_WAGE_TAX),
        "people": [
            {
                "name": people.get(uid) or "You",
                "latest": p["latest"],
                "frequency": p["frequency"],
                "paychecks_left": p["paychecks_left"],
                "stubs_this_year": p["stubs_this_year"],
                "ytd_source": p["ytd_source"],
                "split_estimated": p["split_estimated"],
            }
            for uid, p in pay.items()
            if uid is not None
        ],
        "items": items,
        "federal": {
            "income_cents": income,
            "wages_taxed_cents": wages_taxed,
            "gains_ordinary_cents": gains_ordinary,
            "gains_preferred_cents": gains_preferred,
            "capital_loss_cents": min(-net, loss_limit) if net < 0 else 0,
            "agi_cents": agi,
            "standard_cents": standard,
            "itemizing": itemizing,
            "deduction_cents": deduction,
            "taxable_cents": taxable,
            "ordinary_cents": ordinary,
            "preferred_cents": preferred,
            "ordinary_rows": ordinary_rows,
            "gains_rows": gains_rows,
            "ordinary_tax_cents": ordinary_tax,
            "gains_tax_cents": gains_tax,
            "child_credit_cents": min(child_credit, before_credits),
            "credits_cents": credits,
            "income_tax_cents": income_tax,
            "niit_cents": niit,
            "niit_base_cents": niit_base,
            "tax_cents": federal_total,
            "paid_cents": federal_paid,
            "balance_cents": federal_balance,
            "marginal_rate": marginal,
            "effective_rate": round(federal_total / agi, 4) if agi else 0,
        },
        "fica": {
            "social_security_cents": ss_due,
            "medicare_cents": medicare_due - additional,
            "additional_medicare_cents": additional,
            "due_cents": ss_due + medicare_due,
            "withheld_cents": fica_paid,
            "difference_cents": fica_difference,
            "paid_cents": ss_due + medicare_due - fica_balance,  # as settled on the return
            "balance_cents": fica_balance,
        },
        "state": state,
        "total": {
            "tax_cents": federal_total + ss_due + medicare_due + state_tax,
            "paid_cents": federal_paid + ss_due + medicare_due - fica_balance + state_paid,
            "balance_cents": federal_balance + fica_balance + state["balance_cents"],
        },
        "hints": hints,
        "hsa": hsa | {"taxed_cents": own_over + employer_over},
        "sources": fed["sources"],
        "assumptions": assumptions(t, year, status, prof, split_estimated),
    }


def assumptions(t: dict, year: int, status: str, prof: dict, split_estimated: bool) -> list[str]:
    out = []
    if t["year"] != year:
        out.append(f"Uses the {t['year']} tax tables; {year}'s aren't in the app yet.")
    if prof["filing_status"] is None:
        out.append("Filing status not set yet, so the estimate files as single.")
    out += [
        "Pay still to come repeats your typical paycheck on your pay schedule. Bonuses and raises aren't guessed.",
        "Withholding still to come is the same per paycheck as now; Social Security and Medicare follow the rates.",
        "Capital losses beyond the yearly limit, and losses carried from earlier years, aren't counted.",
        "Not covered: AMT, self-employment tax, the refundable part of the child tax credit, deduction phase-outs, "
        "local taxes, and a refund of extra Social Security from working two jobs.",
        "An estimate to plan with, not tax advice. Your return, or a tax professional, has the final word.",
    ]
    if split_estimated:
        out.insert(0, "Some stubs only gave total taxes, so they're split into federal, state, Social Security and "
                      "Medicare by the usual rates.")  # fmt: skip
    return out


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.0f}"


def short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def build(db: DbSession, viewer_id: int, today: date) -> dict:
    return estimate(table_for(today.year), profile(db), facts(db, viewer_id, today), today)


def stubs_by_person(db: DbSession) -> dict[int | None, list[Paycheck]]:
    stubs: dict[int | None, list[Paycheck]] = {}
    for p in db.scalars(select(Paycheck).order_by(Paycheck.date.desc(), Paycheck.id.desc())):
        stubs.setdefault(p.user_id, []).append(p)
    return stubs


def hsa_for(db: DbSession, viewer_id: int | None, today: date) -> dict | None:
    """The HSA check on its own (for Plan), without the rest of the estimate."""
    from financeapp import plan

    stubs = stubs_by_person(db)
    if not any(stubs.values()):
        return None
    t = table_for(today.year)
    prof = profile(db)
    f = {"viewer_id": viewer_id, "stubs": stubs}
    pay, primary = people_pay(t, prof, f, today.year)
    full = {k: sum(p["full"][k] for p in pay.values()) for k in KINDS}
    left = pay[primary]["paychecks_left"] if primary in pay else 0
    birth = plan.preferences(db).get("birth_year")
    return hsa_check(t, prof, birth, today.year, full, prof["amounts"].get("hsa_outside", 0), left)
