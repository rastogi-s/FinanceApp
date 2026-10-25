"""The Income over time page: pay for each year by source (base, bonus, stock, other), what the employer put in on
top (the 401(k) match and HSA money), how each year grew on the last, and whether raises kept up with prices.

Where each year's numbers come from, best first:
- typed in (or filled from a W-2 or the year's last pay stub): replaces what the app works out, field by field;
- the year's last pay stub with a year-to-date column: gross pay, stock and bonus pay, the match and HSA money for
  the whole year so far, exactly as the employer counted them;
- each pay stub of the year, added up;
- paychecks that landed without a stub: before-tax pay from the household's usual stub, scaled to what landed (a
  paycheck well over the usual one is a bonus). With no stub at all, take-home pay, and the page says so.
Linked accounts usually reach back about two years, so older years are typed in or uploaded.

Base pay is gross pay less stock and bonus pay. A stub that doesn't print a bonus line but is well over the usual
stub has the extra counted as bonus. The pay rate (a year's base at the end of it) is the last regular stub times
paychecks a year, or a raise letter's new salary once it takes effect; raises compare pay rates, so a raise in
March isn't half counted. Inflation is the CPI-U yearly average (data/inflation.json); this year, the latest
12 months.

Raise and bonus letters (read with AI, then checked and saved) say the new base salary, the bonus target and when
it starts. Until a stub after that date is in, this year's "on track for" uses the new salary from its start date,
and Plan's monthly savings adds what the raise keeps after taxes (raise_ahead).

Stored: the "earnings" preference, {"years": {"2024": {field: cents, "note": "..."}}, "letters": [...]}.
"""

import json
import statistics
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget
from financeapp.config import PACKAGE_DIR
from financeapp.models import Paycheck, Preference

FIELDS = ("base", "bonus", "rsu", "other", "match", "employer_hsa")
PAY_FIELDS = ("base", "bonus", "rsu", "other")  # pay; match and employer_hsa are on top of it
LABELS = {
    "base": "Base pay",
    "bonus": "Bonus",
    "rsu": "Stock (RSU vests, ESPP)",
    "other": "Other pay",
    "match": "401(k) match",
    "employer_hsa": "Employer HSA money",
}
HISTORY_YEARS = 4  # how far back linked transactions are looked through
COVERED_DAYS = 3  # a deposit this close after the last stub's date is that stub's paycheck
START_DAYS = 10  # data starting this far into January still counts the whole year
EVEN = 0.25  # a raise within this many points of inflation kept up
MAX_NOTE = 200
MAX_LETTERS = 40


@cache
def inflation() -> dict:
    return json.loads((PACKAGE_DIR / "data" / "inflation.json").read_text())


def inflation_for(year: int) -> dict | None:
    t = inflation()
    if t["latest"]["year"] == year:
        return {"percent": t["latest"]["percent"], "latest": True, "through": t["latest"]["through"]}
    value = t["annual_average"].get(str(year))
    return {"percent": value, "latest": False, "through": None} if value is not None else None


# ---- the household's typed years and letters


def preferences(db: DbSession) -> dict:
    p = db.get(Preference, "earnings")
    value = p.value if p else {}
    return {"years": dict(value.get("years", {})), "letters": list(value.get("letters", []))}


def save(db: DbSession, prefs: dict) -> None:
    db.merge(Preference(key="earnings", value=prefs))
    db.flush()


def set_year(db: DbSession, year: int, changes: dict) -> dict:
    """Changes a year's typed numbers and note: None goes back to what the app works out."""
    prefs = preferences(db)
    row = {**prefs["years"].get(str(year), {}), **changes}
    row = {k: v for k, v in row.items() if v is not None and v != ""}
    if row:
        prefs["years"][str(year)] = row
    else:
        prefs["years"].pop(str(year), None)
    save(db, prefs)
    return prefs


def add_letter(db: DbSession, letter: dict) -> dict:
    prefs = preferences(db)
    letter = {**letter, "id": uuid.uuid4().hex[:10]}
    prefs["letters"] = [*prefs["letters"], letter][-MAX_LETTERS:]
    save(db, prefs)
    return letter


def remove_letter(db: DbSession, letter_id: str) -> bool:
    prefs = preferences(db)
    kept = [x for x in prefs["letters"] if x.get("id") != letter_id]
    if len(kept) == len(prefs["letters"]):
        return False
    prefs["letters"] = kept
    save(db, prefs)
    return True


def letter_note(letter: dict) -> str:
    """What a letter changed, in a few words: "Promoted to Senior Engineer. Base $150,000. Bonus target 15%."""
    parts = []
    if letter.get("title"):
        parts.append(f"Promoted to {letter['title']}" if letter.get("kind") == "promotion" else letter["title"])
    if letter.get("base_cents"):
        parts.append(f"Base {money(letter['base_cents'])}")
    if letter.get("bonus_target_percent"):
        parts.append(f"Bonus target {letter['bonus_target_percent']:g}%")
    elif letter.get("bonus_target_cents"):
        parts.append(f"Bonus target {money(letter['bonus_target_cents'])}")
    if letter.get("bonus_cents"):
        parts.append(f"Bonus {money(letter['bonus_cents'])}")
    if letter.get("stock_cents"):
        parts.append(f"Stock award {money(letter['stock_cents'])}")
    return ". ".join(parts)


# ---- what the database knows


@dataclass(frozen=True)
class Payday:
    day: date
    cents: int  # take-home, all accounts it landed in
    bonus: bool  # well over the usual paycheck


def paydays(db: DbSession, today: date) -> tuple[list[Payday], date | None]:
    """Every payday the linked accounts saw (a paycheck split across accounts is one), and when their data starts."""
    accounts = budget.counted_accounts(db)
    since = date(today.year - HISTORY_YEARS, 1, 1)
    ledger = budget.Ledger(db, accounts, since)
    recent = db.scalars(select(Paycheck).order_by(Paycheck.date.desc(), Paycheck.id.desc()).limit(12)).all()
    payers = budget.pay_merchants(
        ledger, [(p.date, p.net_cents) for p in recent], budget.preferences(db), budget.latest_splits(recent)
    )
    seen: dict[tuple[str, date], Payday] = {}
    for t in ledger.txns:
        if t.id in payers.paydays and budget.is_pay_deposit(ledger, t, payers):
            name, day, total = payers.paydays[t.id]
            seen.setdefault((name, day), Payday(day, total, payers.is_bonus(t)))
    start = min((t.date for t in ledger.txns), default=None)
    return sorted(seen.values(), key=lambda p: p.day), start


def stubs_of(db: DbSession) -> list[Paycheck]:
    return list(db.scalars(select(Paycheck).order_by(Paycheck.date, Paycheck.id)))


# ---- the arithmetic


def this_of(p: Paycheck) -> dict:
    return (p.detail or {}).get("this") or {}


def ytd_of(p: Paycheck) -> dict:
    return (p.detail or {}).get("ytd") or {}


class Usual:
    """The usual paycheck, from recent regular stubs: before-tax pay, the match and HSA money as shares of
    take-home (to fill in paychecks that landed without a stub), and the regular pay per stub."""

    def __init__(self, stubs: list[Paycheck]) -> None:
        recent = stubs[-12:]
        self.ok = False
        if not recent:
            return
        regular = [p.gross_cents - this_of(p).get("stock", 0) - this_of(p).get("bonus", 0) for p in recent]
        middle = statistics.median(regular)
        usual = [p for p, r in zip(recent, regular, strict=True) if r <= middle * budget.BONUS_FACTOR and p.net_cents]
        net = sum(p.net_cents for p in usual)
        if not net:
            return
        self.ok = True
        self.gross = sum(p.gross_cents for p in usual) / net
        self.taxes = sum(p.taxes_cents for p in usual) / sum(p.gross_cents for p in usual)
        self.match = sum(p.employer_match_cents for p in usual) / net
        self.hsa = sum(this_of(p).get("employer_hsa", 0) for p in usual) / net


def split_stub(p: Paycheck, typical: float | None) -> dict:
    """One stub's pay by source. Without a bonus line, a stub well over the typical one has the extra as bonus."""
    d = this_of(p)
    stock = d.get("stock", 0)
    bonus = d.get("bonus", 0)
    estimated = False
    if not bonus and typical and p.gross_cents - stock > typical * budget.BONUS_FACTOR:
        bonus = round(p.gross_cents - stock - typical)
        estimated = True
    return {
        "base": p.gross_cents - stock - bonus,
        "bonus": bonus,
        "rsu": stock,
        "match": p.employer_match_cents,
        "employer_hsa": d.get("employer_hsa", 0),
        "bonus_estimated": estimated,
    }


def typical_regular(stubs: list[Paycheck]) -> float | None:
    regular = [p.gross_cents - this_of(p).get("stock", 0) - this_of(p).get("bonus", 0) for p in stubs]
    return statistics.median(regular) if len(regular) >= 3 else None


def frequency_of(stubs: list[Paycheck]) -> str | None:
    return next((p.frequency for p in reversed(stubs) if p.frequency), None)


def letters_in_effect(letters: list[dict], on: date) -> list[dict]:
    return sorted(
        (x for x in letters if x.get("effective") and date.fromisoformat(x["effective"]) <= on),
        key=lambda x: x["effective"],
    )


def pay_rate(stubs: list[Paycheck], letters: list[dict], end: date) -> tuple[int | None, str]:
    """Base pay a year at `end`: the last regular stub by then times paychecks a year, or the newest letter's salary
    that took effect after that stub."""
    regular = [p for p in stubs if p.date <= end]
    typical = typical_regular(regular[-6:])
    rate, how, as_of = None, "", None
    for p in reversed(regular):
        s = split_stub(p, typical)
        if s["bonus"] or (s["rsu"] > 0 and s["base"] < (typical or 0) * 0.5):
            continue
        per_year = budget.PER_YEAR.get(p.frequency or frequency_of(regular) or "", 0)
        if per_year and s["base"] > 0:
            rate, how, as_of = s["base"] * per_year, "stub", p.date
        break
    salaried = [x for x in letters_in_effect(letters, end) if x.get("base_cents")]
    if salaried and (as_of is None or date.fromisoformat(salaried[-1]["effective"]) > as_of):
        rate, how = salaried[-1]["base_cents"], "letter"
    return rate, how


def year_row(year: int, stubs: list[Paycheck], days: list[Payday], usual: Usual, start: date | None) -> dict:
    """One year from its stubs and paydays, before anything typed in."""
    ys = [p for p in stubs if p.date.year == year]
    yd = [d for d in days if d.day.year == year]
    out = dict.fromkeys(FIELDS, 0)
    how = dict.fromkeys(FIELDS, "")
    stub_count = len(ys)
    estimated = 0
    take_home = False
    bonus_guessed = False
    through = None
    covered_until = None

    if ys:
        typical = typical_regular(ys) or typical_regular(stubs[-6:])
        splits = [split_stub(p, typical) for p in ys]
        sums = {k: sum(s[k] for s in splits) for k in FIELDS if k != "other"}
        bonus_guessed = any(s["bonus_estimated"] for s in splits)
        last = ys[-1]
        ytd = ytd_of(last)
        gross_sum = sum(p.gross_cents for p in ys)
        if ytd.get("gross") and ytd["gross"] >= gross_sum - 100:
            scale = ytd["gross"] / gross_sum if gross_sum else 1
            rsu = ytd.get("stock", sums["rsu"])
            bonus = ytd.get("bonus", sums["bonus"])
            out |= {
                "rsu": rsu,
                "bonus": bonus,
                "base": ytd["gross"] - rsu - bonus,
                "match": ytd.get("match", round(sums["match"] * scale)),
                "employer_hsa": ytd.get("employer_hsa", round(sums["employer_hsa"] * scale)),
            }
            how |= dict.fromkeys(("base", "bonus", "rsu", "match", "employer_hsa"), "ytd")
            covered_until = last.date
        else:
            out |= {k: sums[k] for k in sums}
            how |= dict.fromkeys(("base", "bonus", "rsu", "match", "employer_hsa"), "stubs")
        through = last.date

    for d in yd:
        if covered_until is not None:
            if d.day <= covered_until + timedelta(days=COVERED_DAYS):
                continue
        elif any(abs((p.date - d.day).days) <= budget.MATCH_DAYS for p in ys):
            continue
        estimated += 1
        through = max(through or d.day, d.day)
        if usual.ok:
            gross = round(d.cents * usual.gross)
            out["bonus" if d.bonus else "base"] += gross
            out["match"] += round(d.cents * usual.match)
            out["employer_hsa"] += round(d.cents * usual.hsa)
            how["bonus" if d.bonus else "base"] = how["bonus" if d.bonus else "base"] or "estimated"
        else:
            take_home = True
            out["bonus" if d.bonus else "base"] += d.cents
            how["bonus" if d.bonus else "base"] = "take_home"

    whole_from_start = covered_until is not None or (start is not None and start <= date(year, 1, START_DAYS))
    return {
        "year": year,
        "amounts": out,
        "how": how,
        "stubs": stub_count,
        "estimated_paychecks": estimated,
        "take_home": take_home,
        "bonus_guessed": bonus_guessed,
        "from_start": whole_from_start,
        "through": through,
        "has_data": bool(ys or yd),
    }


def project(
    row: dict, stubs: list[Paycheck], days: list[Payday], usual: Usual, letters: list[dict], today: date
) -> dict | None:
    """This year on track: so far, plus the paychecks left on the schedule at the usual pay (a letter's new salary
    from its start date), plus a one-time bonus a letter says is coming."""
    frequency = frequency_of(stubs)
    per_year = budget.PER_YEAR.get(frequency or "", 0)
    seen = [p.date for p in stubs if p.date.year == today.year] + [d.day for d in days if d.day.year == today.year]
    last_day = max(seen, default=None)
    if not per_year or last_day is None:
        return None
    recent = stubs[-6:]
    typical = typical_regular(recent)
    if typical is None and recent:
        typical = split_stub(recent[-1], None)["base"]
    if not typical and usual.ok and days:
        regular = [d.cents for d in days[-6:] if not d.bonus]
        typical = statistics.median(regular) * usual.gross if regular else None
    if not typical:
        return None
    match_share = sum(p.employer_match_cents for p in recent) / max(sum(p.gross_cents for p in recent), 1)
    hsa_per = statistics.median([this_of(p).get("employer_hsa", 0) for p in recent]) if recent else 0
    adds = dict.fromkeys(FIELDS, 0)
    left = 0
    raise_used = None
    d = last_day
    end = date(today.year, 12, 31)
    salaried = sorted((x for x in letters if x.get("base_cents") and x.get("effective")), key=lambda x: x["effective"])
    while (d := budget.next_payday(d, frequency)) <= end:
        left += 1
        base = typical
        for x in salaried:
            starts = date.fromisoformat(x["effective"])
            if starts <= d and starts > (stubs[-1].date if stubs else date.min):
                base = x["base_cents"] / per_year
                raise_used = x
        adds["base"] += base
        adds["match"] += base * match_share
        adds["employer_hsa"] += hsa_per
    for x in letters:
        if x.get("bonus_cents") and x.get("effective"):
            when = date.fromisoformat(x["effective"])
            if last_day < when <= end:
                adds["bonus"] += x["bonus_cents"]
    full = {k: row["amounts"][k] + round(adds[k]) for k in FIELDS}
    return {
        "amounts": full,
        "paychecks_left": left,
        "per_paycheck_cents": round(typical),
        "raise_from": raise_used["effective"] if raise_used else None,
    }


def growth(now: int, before: int) -> float | None:
    return round((now / before - 1) * 100, 1) if before > 0 and now > 0 else None


def assemble(stubs: list[Paycheck], days: list[Payday], start: date | None, prefs: dict, today: date) -> dict:
    """The whole page, from the stubs (oldest first), paydays and what was typed in."""
    usual = Usual(stubs)
    letters = sorted(prefs["letters"], key=lambda x: x.get("effective") or "")
    typed_years = {int(y): v for y, v in prefs["years"].items() if str(y).isdigit()}
    first_years = [p.date.year for p in stubs] + [d.day.year for d in days] + list(typed_years)
    first = min(first_years, default=today.year)
    rows = []
    for year in range(first, today.year + 1):
        row = year_row(year, stubs, days, usual, start)
        typed = typed_years.get(year, {})
        for k in FIELDS:
            if k in typed:
                row["amounts"][k] = typed[k]
                row["how"][k] = typed.get("source") or "typed"
        row["typed"] = [k for k in FIELDS if k in typed]
        row["note"] = typed.get("note", "")
        row["source"] = typed.get("source")
        row["has_data"] = row["has_data"] or bool(row["typed"])
        if not row["has_data"] and year != today.year:
            continue
        current = year == today.year
        # A year counts whole when its data starts in January, or its base pay was typed in.
        row["current"] = current
        row["complete"] = not current and (row["from_start"] or "base" in typed)
        row["projected"] = project(row, stubs, days, usual, letters, today) if current and not typed else None
        rate, rate_how = pay_rate(stubs, letters, min(date(year, 12, 31), today))
        if rate_how == "stub" and not any(p.date.year == year for p in stubs):
            rate = None  # the stubs are from another year
        if rate is None and row["complete"] and not row["take_home"]:
            rate, rate_how = row["amounts"]["base"], "total"
        row["rate_cents"], row["rate_how"] = rate, rate_how
        row["events"] = [
            {"id": x["id"], "effective": x["effective"], "text": letter_note(x), "kind": x.get("kind")}
            for x in letters
            if x.get("effective", "")[:4] == str(year)
        ]
        rows.append(row)

    out = []
    for row in rows:
        a = (row["projected"] or {}).get("amounts") or row["amounts"]
        pay = sum(a[k] for k in PAY_FIELDS)
        comp = pay + a["match"] + a["employer_hsa"]
        so_far = row["amounts"]
        item = {
            "year": row["year"],
            "current": row["current"],
            "complete": row["complete"],
            "through": row["through"].isoformat() if row["through"] else None,
            "take_home": row["take_home"],
            "stubs": row["stubs"],
            "estimated_paychecks": row["estimated_paychecks"],
            "bonus_guessed": row["bonus_guessed"],
            "typed": row["typed"],
            "source": row["source"],
            "note": row["note"],
            "events": row["events"],
            "sources": [
                {"key": k, "label": LABELS[k], "cents": so_far[k], "how": row["how"][k], "full_cents": a[k]}
                for k in FIELDS
            ],
            "pay_cents": sum(so_far[k] for k in PAY_FIELDS),
            "comp_cents": sum(so_far[k] for k in FIELDS),
            "full_pay_cents": pay,
            "full_comp_cents": comp,
            "projected": row["projected"] and {k: v for k, v in row["projected"].items() if k != "amounts"},
            "rate_cents": row["rate_cents"],
            "rate_how": row["rate_how"],
            "growth": None,
            "raise": None,
        }
        prev = out[-1] if out else None
        comparable = prev is not None and prev["complete"] and prev["year"] == row["year"] - 1
        same_basis = comparable and not row["take_home"] and not prev["take_home"]
        if same_basis and (row["complete"] or row["projected"]):
            item["growth"] = {
                "pay": growth(pay, prev["full_pay_cents"]),
                "base": growth(a["base"], next(s["full_cents"] for s in prev["sources"] if s["key"] == "base")),
                "comp": growth(comp, prev["full_comp_cents"]),
                "on_track": bool(row["projected"]),
            }
        before = prev["rate_cents"] if prev and prev["year"] == row["year"] - 1 else None
        if before and item["rate_cents"] and not row["take_home"]:
            percent = round((item["rate_cents"] / before - 1) * 100, 1)
            infl = inflation_for(row["year"])
            raise_ = {"percent": percent, "from_cents": before, "inflation": infl, "real": None, "verdict": None}
            if infl is not None:
                real = round(((1 + percent / 100) / (1 + infl["percent"] / 100) - 1) * 100, 1)
                raise_["real"] = real
                raise_["verdict"] = "even" if abs(real) < EVEN else ("ahead" if real > 0 else "behind")
            item["raise"] = raise_
        out.append(item)
    out.reverse()  # newest first

    now = pay_now(stubs, letters, today, usual)
    return {
        "year": today.year,
        "years": out,
        "now": now,
        "letters": letters,
        "has_stubs": bool(stubs),
        "linked_from": start.isoformat() if start else None,
        "labels": LABELS,
        "inflation": {"sources": inflation()["sources"], "note": inflation()["note"]},
        "demo": False,
    }


def pay_now(stubs: list[Paycheck], letters: list[dict], today: date, usual: Usual) -> dict | None:
    """Base pay a year today, the bonus target, and a raise still to come."""
    rate, how = pay_rate(stubs, letters, today)
    target = None
    for x in letters_in_effect(letters, today):
        if x.get("bonus_target_percent") or x.get("bonus_target_cents"):
            target = x
    coming = next((x for x in letters if x.get("base_cents") and (x.get("effective") or "") > today.isoformat()), None)
    if rate is None and target is None and coming is None:
        return None
    bonus_cents = None
    if target:
        pct = target.get("bonus_target_percent")
        bonus_cents = round(rate * pct / 100) if pct and rate else target.get("bonus_target_cents")
    return {
        "base_cents": rate,
        "base_how": how,
        "bonus_target_percent": target.get("bonus_target_percent") if target else None,
        "bonus_target_cents": bonus_cents,
        "cash_cents": (rate or 0) + (bonus_cents or 0) if rate else None,
        "coming": {"effective": coming["effective"], "base_cents": coming["base_cents"]} if coming else None,
        "keeps_share": round(1 - usual.taxes, 3) if usual.ok else None,
    }


def build(db: DbSession, today: date) -> dict:
    days, start = paydays(db, today)
    return assemble(stubs_of(db), days, start, preferences(db), today)


def raise_ahead(db: DbSession, today: date) -> dict | None:
    """A saved raise the pay stubs don't show yet (it starts after the newest stub), for Plan's monthly savings: the
    extra base pay a month, less taxes at the usual stub's rate. None once a stub from after it is in."""
    stubs = stubs_of(db)
    if not stubs:
        return None
    prefs = preferences(db)
    newest = stubs[-1].date
    ahead = [
        x
        for x in prefs["letters"]
        if x.get("base_cents")
        and x.get("effective")
        and newest < date.fromisoformat(x["effective"]) <= today + timedelta(days=366)
    ]
    if not ahead:
        return None
    letter = max(ahead, key=lambda x: x["effective"])
    rate, _ = pay_rate(stubs, [], newest)
    usual = Usual(stubs)
    if not rate or not usual.ok or letter["base_cents"] <= rate:
        return None
    extra = (letter["base_cents"] - rate) / 12
    return {
        "cents": round(extra * (1 - usual.taxes)),
        "gross_cents": round(extra),
        "effective": letter["effective"],
        "base_cents": letter["base_cents"],
        "from_cents": rate,
    }


def money(cents: int) -> str:
    return f"${cents / 100:,.0f}"
