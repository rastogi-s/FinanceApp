"""Which month a bill payment counts toward, which isn't always the month it was paid in.

Rent is paid ahead. Rent paid on Oct 28 is November's rent, so it counts in November's budget, savings rate and
forecast. Each rent payee's usual payment day is learned from its own payments. When that day falls in the month's
last week, each payment counts toward the month after the one it's nearest to: rent paid on Nov 1 by someone who
usually pays on the 28th is still November's. A rent payee without enough history counts a payment made in the
last week of a month toward the next month.

Utilities are paid around the same point each month, and that point moves with the billing cycle. A biller
usually paid around the 28th is sometimes paid on the 30th and sometimes on the 2nd. Each biller's usual day is
learned from its recent payments, wrapping around the end of the month: the 27th, 30th and 2nd are about the
same day. Each payment counts toward the month of the usual day it's nearest to, so a payment on Nov 2 that
belongs with the Oct 28 one is October's bill, and every month gets exactly one. A biller with no steady day (it
wanders by more than SPREAD_DAYS), or one paid every two or three months, counts in the month it was paid.

A change by hand (Transaction.budget_month) always wins. Only money out counts this way: refunds, income and
transfers stay in the month they happened.
"""

import itertools
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp.categorize import merchant_key
from financeapp.models import Category, Transaction

RENT_CATEGORY = "Rent & mortgage"
HISTORY_DAYS = 400
RECENT = 6  # the latest payments set a payee's usual day
MIN_PAYMENTS = 3
SPREAD_DAYS = 5  # a usual day only when most payments land this close to it
NEAR_DAYS = 12  # a payment further than this from any usual day counts in the month it was paid
LATE_FROM_DAY = 25  # rent usually paid this day or later (or in the month's last week) is paid ahead
LAST_WEEK = 7  # without history, rent paid in a month's last week is the next month's
SAME_BILL_DAYS = 6  # payments this close together are one bill
MONTHLY_GAPS = (24, 38)
LOOKAROUND_DAYS = 31  # how far either side of a month a payment can be that counts toward it


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    m = d.year * 12 + d.month - 1 + n
    return date(m // 12, m % 12 + 1, 1)


def days_in(month: date) -> int:
    return (add_months(month, 1) - month_start(month)).days


def signed_day(d: date) -> int:
    """The 1st to 15th as they are; later days counted back from the month's end: the last day is -1, the one
    before it -2. The 30th of a 31-day month and the 2nd are then 4 days apart, as they are on a calendar."""
    return d.day if d.day <= 15 else d.day - days_in(d) - 1


def on_day(month: date, day: int) -> date:
    """`day` (as usual_day gives it) in that month: a positive day is that date (a short month's last day for the
    29th to 31st), a negative one counts back from the month's end."""
    if day > 0:
        return month.replace(day=min(day, days_in(month)))
    return add_months(month, 1) + timedelta(days=day)


def usual_day(dates: list[date]) -> int | None:
    """The day a payee is usually paid, from the latest payments, or None when it doesn't keep to one. Days are
    compared both as calendar days and counted back from the month's end, and whichever keeps them closer wins,
    so a habit around the turn of the month (the 28th, the 30th, the 2nd) still has one usual day."""
    recent = sorted(dates)[-RECENT:]
    if len(recent) < MIN_PAYMENTS:
        return None
    best = None
    for days in ([d.day for d in recent], [signed_day(d) for d in recent]):
        mid = round(statistics.median(days))
        spread = statistics.median(abs(d - mid) for d in days)
        if spread <= SPREAD_DAYS and (best is None or spread < best[1]):
            best = (mid, spread)
    if best is None:
        return None
    day = best[0]
    if day == 0:  # the median of the 1st and the last day: call it the month's last day
        day = -1
    return day


def nearest_month(d: date, day: int) -> tuple[date, int]:
    """The month whose usual day `d` is nearest to, and how many days away it is."""
    m = month_start(d)
    options = [(abs((d - on_day(month, day)).days), month) for month in (add_months(m, -1), m, add_months(m, 1))]
    away, month = min(options)
    return month, away


def is_monthly(dates: list[date]) -> bool:
    """Paid about once a month (payments a few days apart are one bill), not every two or three months."""
    bills: list[date] = []
    for d in sorted(dates):
        if not bills or (d - bills[-1]).days > SAME_BILL_DAYS:
            bills.append(d)
    gaps = [(b - a).days for a, b in itertools.pairwise(bills)][-RECENT:]
    if not gaps:
        return False
    lo, hi = MONTHLY_GAPS
    return lo <= statistics.median(gaps) <= hi


def paid_ahead(day: int) -> bool:
    """A usual day in the month's last week: rent paid then is for the month after."""
    return -LAST_WEEK <= day < 0 or day >= LATE_FROM_DAY


class Covers:
    """Works out the month each bill payment counts toward. Learns each rent payee's and utility's usual day from
    the last HISTORY_DAYS of payments (from every account given), once, when it's made."""

    def __init__(
        self,
        db: DbSession,
        account_ids: list[int],
        today: date,
        categories: dict[int, Category] | None = None,
    ) -> None:
        from financeapp import plan  # plan imports budget, which imports this module

        self._kind_of = plan.recurring_kind
        self.categories = categories if categories is not None else {c.id: c for c in db.scalars(select(Category))}
        self.today = today
        rows = (
            db.scalars(
                select(Transaction).where(
                    Transaction.account_id.in_(account_ids),
                    Transaction.amount_cents < 0,
                    Transaction.transfer_group_id.is_(None),
                    Transaction.date >= self.today - timedelta(days=HISTORY_DAYS),
                )
            ).all()
            if account_ids
            else []
        )
        by_payee: dict[tuple[str, str], list[date]] = defaultdict(list)
        for t in rows:
            kind = self.kind(t)
            if kind and (key := merchant_key(t.merchant or t.description or "")):
                by_payee[(kind, key)].append(t.date)
        self.days: dict[tuple[str, str], int] = {}
        for payee, dates in by_payee.items():
            if is_monthly(dates) and (day := usual_day(dates)) is not None:
                self.days[payee] = day

    def kind(self, t: Transaction) -> str | None:
        """ "rent", "utility", or None for anything that counts in the month it was paid."""
        c = self.categories.get(t.category_id) if t.category_id else None
        if c is not None and c.kind in ("transfer", "income"):
            return None
        if c is not None and c.name == RENT_CATEGORY:
            return "rent"
        if self._kind_of(t.merchant or "", c) == "utility":
            return "utility"
        return None

    def auto(self, t: Transaction) -> date:
        """The month a payment counts toward when nobody has said otherwise."""
        posted = month_start(t.date)
        if t.amount_cents >= 0 or t.transfer_group_id is not None:
            return posted
        kind = self.kind(t)
        if kind is None:
            return posted
        day = self.days.get((kind, merchant_key(t.merchant or t.description or "")))
        if day is None:
            if kind == "rent" and t.date.day > days_in(posted) - LAST_WEEK:
                return add_months(posted, 1)
            return posted
        month, away = nearest_month(t.date, day)
        if away > NEAR_DAYS:
            return add_months(posted, 1) if kind == "rent" and paid_ahead(signed_day(t.date)) else posted
        return add_months(month, 1) if kind == "rent" and paid_ahead(day) else month

    def month_of(self, t: Transaction) -> date:
        if t.budget_month is not None:
            return month_start(t.budget_month)
        return self.auto(t)

    def usual(self, t: Transaction) -> int | None:
        kind = self.kind(t)
        return self.days.get((kind, merchant_key(t.merchant or t.description or ""))) if kind else None
