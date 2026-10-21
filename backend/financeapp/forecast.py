"""Where each budget category is heading by the end of the month.

Two kinds of spending, projected differently.

Charges that come once a month (the same merchant once in each of at least 2 of the last 3 full months: rent, a
phone bill, a gym) are expected once more if they haven't come yet, at their usual amount. Once one has come it's
just part of what's spent. One that is more than a week past its usual day is taken as skipped.

Everything else follows a daily pace: what this month's pace says, leaning on the category's usual month early on
and more on this month as it goes (half and half by mid-month). A single big purchase (over a fifth of the
limit) counts as spent but isn't repeated in the pace: one new sofa doesn't mean another every week. Bills don't
have a pace: whatever part of their usual month isn't a known charge is expected once, if it hasn't come yet.

A projection is "heading over" only when it ends at least $5 or 3% of the limit past it, so a rounding-sized
overage doesn't raise a flag. Without any full month of history it waits until the 10th to say anything.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from financeapp.categorize import normalize_merchant

RECURRING_MONTHS = 3  # full months looked at for once-a-month charges
RECURRING_MIN = 2  # seen in at least this many of them
LATE_DAYS = 7  # this far past its usual day, a charge is taken as skipped this month
LUMP_SHARE = 0.20  # a single purchase over this share of the limit isn't part of the pace
OVER_CENTS = 500
OVER_SHARE = 0.03
CLOSE_SHARE = 0.90
NO_HISTORY_FROM_DAY = 10
MAX_DUE = 5


@dataclass
class Recurring:
    merchant: str
    cents: int
    day: int


def days_in(month: date) -> int:
    nxt = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return (nxt - month).days


def margin(limit_cents: int) -> int:
    return max(OVER_CENTS, round(limit_cents * OVER_SHARE))


def status(projected: int, spent: int, limit: int, kind: str = "flexible") -> str:
    """over (already), heading_over, close (everyday only: a bill landing on its limit is just paid) or on_track."""
    if limit <= 0:
        return "on_track"
    if spent > limit:
        return "over"
    if projected >= limit + margin(limit):
        return "heading_over"
    if kind == "flexible" and projected >= limit * CLOSE_SHARE:
        return "close"
    return "on_track"


def recurring(charges: dict[date, list[tuple[str, int, int]]], months: list[date]) -> dict[str, Recurring]:
    """charges: month -> [(merchant key, cents, day)] in one category. Returns its once-a-month charges."""
    window = months[-RECURRING_MONTHS:]
    seen: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for m in window:
        per: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for key, cents, day in charges.get(m, []):
            per[key].append((cents, day))
        for key, items in per.items():
            if len(items) == 1:  # a shop visited weekly isn't a monthly charge
                seen[key].append(items[0])
            else:
                seen[key].append((-1, 0))
    out = {}
    for key, items in seen.items():
        if not key or len(items) < RECURRING_MIN or any(c < 0 for c, _ in items):
            continue
        out[key] = Recurring(
            key, round(statistics.median(c for c, _ in items)), round(statistics.median(d for _, d in items))
        )
    return out


def project(
    *,
    kind: str,
    limit_cents: int,
    spent_cents: int,
    month: date,
    today: date,
    months: list[date],
    charges: dict[date, list[tuple[str, int, int]]],
    names: dict[str, str],
) -> dict | None:
    """One category's month-end projection. charges: month -> [(merchant key, cents out, day)], this month
    included; names: merchant key -> how to show it."""
    n = days_in(month)
    day = min(today.day, n)
    gone = day - 1  # today isn't over (or synced) yet: it counts as a day still to come
    left = n - gone
    if not months and day < NO_HISTORY_FROM_DAY:
        return None
    monthly = recurring(charges, months)
    this = charges.get(month, [])
    spent = spent_cents  # the budget's own number, refunds and all

    paid_keys = {k for k, _, _ in this if k in monthly}
    due = [r for k, r in monthly.items() if k not in paid_keys and r.day >= day - LATE_DAYS]
    due_cents = sum(r.cents for r in due)

    other_now = sum(c for k, c, _ in this if k not in monthly)
    usual_other = (
        statistics.median(max(sum(c for k, c, _ in charges.get(m, []) if k not in monthly), 0) for m in months)
        if months
        else 0
    )
    if kind == "fixed":
        rest = max(round(usual_other) - other_now, 0)
    else:
        lump = limit_cents * LUMP_SHARE
        paced = sum(c for k, c, _ in this if k not in monthly and (limit_cents <= 0 or c <= lump))
        pace_now = paced / gone if gone else 0
        w = gone / n
        daily = w * pace_now + (1 - w) * (usual_other / n) if months else pace_now
        rest = max(round(daily * left), 0)

    projected = spent + due_cents + rest
    s = status(projected, spent, limit_cents, kind)
    return {
        "projected_cents": projected,
        "over_cents": max(projected - limit_cents, 0),
        "status": s,
        "due_cents": due_cents,
        "due": [
            {"merchant": names.get(r.merchant, r.merchant), "cents": r.cents, "day": min(r.day, n)}
            for r in sorted(due, key=lambda r: r.day)[:MAX_DUE]
        ],
        "pace_cents": rest,
        "days_left": left,
    }


def for_lines(ledger, months: list[date], month: date, today: date, lines: list[dict]) -> None:
    """Adds a "forecast" to each budget line (None when it's too early to say)."""
    wanted = {line["category_id"] for line in lines}
    since = months[-RECURRING_MONTHS:][0] if months else month
    charges: dict[int, dict[date, list[tuple[str, int, int]]]] = defaultdict(lambda: defaultdict(list))
    names: dict[str, str] = {}
    for t in ledger.txns:
        if t.category_id not in wanted or t.date < since or t.amount_cents >= 0 or not ledger.is_spending(t):
            continue
        key = normalize_merchant(t.merchant or t.description or "")
        names.setdefault(key, t.merchant or t.description or "")
        m = t.date.replace(day=1)
        charges[t.category_id][m].append((key, -t.amount_cents, t.date.day))
    for line in lines:
        line["forecast"] = project(
            kind=line["kind"],
            limit_cents=line["limit_cents"],
            spent_cents=line["spent_cents"],
            month=month,
            today=today,
            months=months,
            charges=charges[line["category_id"]],
            names=names,
        )


def summary(lines: list[dict]) -> dict | None:
    with_forecast = [line for line in lines if line.get("forecast")]
    if not with_forecast:
        return None
    return {
        "projected_cents": sum(line["forecast"]["projected_cents"] for line in with_forecast),
        "limit_cents": sum(line["limit_cents"] for line in with_forecast),
        "heading_over": [line["category_id"] for line in with_forecast if line["forecast"]["status"] == "heading_over"],
        "days_left": with_forecast[0]["forecast"]["days_left"],
    }
