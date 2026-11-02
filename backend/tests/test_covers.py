from datetime import date

import pytest
from sqlalchemy import select

from financeapp import bills, budget, covers
from financeapp.models import Category, Transaction
from tests.conftest import csrf, db_of, needs_db
from tests.test_budget import add_transactions, fixed_today, line  # noqa: F401 (fixed_today: October 21)

# Rent paid ahead, usually on the 28th; once on the 1st of the month it was for (July).
RENT = [
    ("2026-04-28", 0),
    ("2026-05-28", 0),
    ("2026-07-01", 0),
    ("2026-07-28", 0),
    ("2026-08-27", 0),
    ("2026-09-28", 0),
]
# Power, paid near the end of the month it's for, but the billing cycle moves: July's was paid on Aug 2 and
# September's on Oct 1.
POWER = [("2026-05-28", 150), ("2026-06-27", 160), ("2026-08-02", 210), ("2026-08-29", 190), ("2026-10-01", 170)]


def test_usual_day_wraps_around_the_month_end():
    days = [date(2026, 5, 28), date(2026, 6, 27), date(2026, 8, 2), date(2026, 8, 29), date(2026, 10, 1)]
    day = covers.usual_day(days)
    assert day is not None and day < 0  # counted back from the month's end
    assert covers.nearest_month(date(2026, 8, 2), day)[0] == date(2026, 7, 1)
    assert covers.nearest_month(date(2026, 8, 29), day)[0] == date(2026, 8, 1)
    assert covers.usual_day([date(2026, m, 14) for m in range(5, 10)]) == 14
    assert covers.usual_day([date(2026, 5, 3), date(2026, 6, 20), date(2026, 7, 11), date(2026, 8, 27)]) is None
    assert covers.on_day(date(2026, 2, 1), -1) == date(2026, 2, 28) and covers.on_day(date(2026, 2, 1), 30).day == 28
    assert covers.is_monthly([date(2026, m, 1) for m in range(1, 7)])
    assert not covers.is_monthly([date(2026, m, 1) for m in range(1, 12, 2)])  # every two months


def rows() -> list:
    out = [(d, "Maple Court Apartments", -2000, "Rent & mortgage") for d, _ in RENT]
    out += [(d, "DUKE ENERGY ONLINE PMT", -v, "Utilities") for d, v in POWER]
    out += [(f"2026-{m:02}-12", "Whole Foods", -400, "Groceries") for m in range(5, 11)]
    out += [(f"2026-{m:02}-15", "ACME PAYROLL", 5000, "Paycheck") for m in range(5, 11)]
    return out


def months_spent(client, category: str) -> dict[str, int]:
    with db_of(client).session() as db:
        cid = db.scalar(select(Category.id).where(Category.name == category))
        ledger = budget.Ledger(db, budget.counted_accounts(db), date(2026, 5, 1))
        return {m.isoformat()[:7]: v for (c, m), v in sorted(ledger.spent.items()) if c == cid}


@needs_db
def test_each_month_gets_one_rent_and_one_power_bill(client, home):
    home.setup()
    add_transactions(client, rows())
    # Rent paid on the 28th is next month's; the July 1 one is still July's. One each month, May to October.
    assert months_spent(client, "Rent & mortgage") == {f"2026-{m:02}": 200_000 for m in range(5, 11)}
    # Power paid on Aug 2 was July's, on Oct 1 September's: one bill a month, none yet for October.
    assert months_spent(client, "Utilities") == {
        "2026-05": 15_000,
        "2026-06": 16_000,
        "2026-07": 21_000,
        "2026-08": 19_000,
        "2026-09": 17_000,
    }

    b = client.get("/api/budget").json()
    rent, power = line(b, "Rent & mortgage"), line(b, "Utilities")
    assert rent["spent_cents"] == 200_000 and rent["limit_cents"] == 200_000  # the draft: a steady $2,000
    assert rent["forecast"]["due"] == []  # October's rent went out on Sep 28: nothing still to come
    assert power["spent_cents"] == 0 and [d["merchant"] for d in power["forecast"]["due"]] == ["DUKE ENERGY ONLINE PMT"]
    # Budget says which bills aren't in their calendar month: September's rent counted here, October's power
    # (paid on the 1st) counted in September.
    moved = {(m["merchant"], m["date"], m["counts_toward"], m["into"]) for m in b["moved"]}
    assert moved == {
        ("Maple Court Apartments", "2026-09-28", "2026-10-01", True),
        ("DUKE ENERGY ONLINE PMT", "2026-10-01", "2026-09-01", False),
    }

    # The savings rate's months each have one rent, and the money that left early is said where it went.
    history = {h["month"][:7]: h for h in b["savings_history"]}
    assert {m: h["spending_cents"] for m, h in history.items()} == {
        "2026-05": 2_000_00 + 150_00 + 400_00,
        "2026-06": 2_000_00 + 160_00 + 400_00,
        "2026-07": 2_000_00 + 210_00 + 400_00,
        "2026-08": 2_000_00 + 190_00 + 400_00,
        "2026-09": 2_000_00 + 170_00 + 400_00,
    }
    sep = [x for x in history["2026-09"]["breakdown"]["went"] if x["kind"] == "paid_ahead"]
    assert {x["label"]: x["cents"] for x in sep} == {
        "Paid ahead for October": 200_000,  # October's rent left on Sep 28
        "Paid in August for September": -200_000,  # September's left on Aug 27
        "Paid in October for September": -17_000,  # September's power left on Oct 1
    }


@needs_db
def test_spending_from_budget_adds_up_to_the_row(client, home):
    home.setup()
    add_transactions(client, rows())
    b = client.get("/api/budget").json()
    for name in ("Rent & mortgage", "Utilities", "Groceries"):
        row = line(b, name)
        found = client.get(
            "/api/transactions",
            params={"category": row["category_id"], "start": "2026-10-01", "end": "2026-10-31", "budget": True},
        ).json()
        assert found["out_cents"] - found["in_cents"] == row["spent_cents"], name
    rent = client.get(
        "/api/transactions",
        params={"category": line(b, "Rent & mortgage")["category_id"], "start": "2026-10-01", "budget": True},
    ).json()
    [t] = rent["transactions"]
    assert t["date"] == "2026-09-28" and t["counts_toward"] == "2026-10-01" and not t["counts_by_hand"]
    # The calendar month (no budget flag) is what was paid in it.
    plain = client.get(
        "/api/transactions",
        params={"category": line(b, "Rent & mortgage")["category_id"], "start": "2026-10-01"},
    ).json()
    assert plain["count"] == 0


@needs_db
def test_pick_the_month_by_hand(client, home):
    home.setup()
    add_transactions(client, rows())
    with db_of(client).session() as db:
        aug = db.scalar(select(Transaction).where(Transaction.date == date(2026, 8, 27)))
        groceries = db.scalar(select(Transaction).where(Transaction.merchant == "Whole Foods"))
        aug_id, groceries_id = aug.id, groceries.id
    h = csrf(client)
    r = client.patch(f"/api/transactions/{aug_id}/month", json={"month": "2026-08-01"}, headers=h).json()
    assert r == {"id": str(aug_id), "counts_toward": None, "counts_by_hand": True}
    spent = months_spent(client, "Rent & mortgage")
    assert spent["2026-08"] == 400_000 and "2026-09" not in spent
    r = client.patch(f"/api/transactions/{aug_id}/month", json={"month": None}, headers=h).json()
    assert r["counts_toward"] == "2026-09-01" and not r["counts_by_hand"]
    assert months_spent(client, "Rent & mortgage")["2026-09"] == 200_000
    # Any purchase can be moved a month (a bill the app doesn't know), but not further.
    ok = client.patch(f"/api/transactions/{groceries_id}/month", json={"month": "2026-06-01"}, headers=h)
    assert ok.status_code == 200 and ok.json()["counts_by_hand"]
    far = client.patch(f"/api/transactions/{groceries_id}/month", json={"month": "2026-09-01"}, headers=h)
    assert far.status_code == 422


@needs_db
def test_power_paid_early_is_not_late(client, home):
    """A bill usually paid on the 2nd that's paid on the 28th before isn't late next month."""
    home.setup()
    add_transactions(client, [(f"2026-{m:02}-02", "City Water", -60, "Utilities") for m in range(3, 10)])
    add_transactions(client, [("2026-09-28", "City Water", -60, "Utilities")])
    with db_of(client).session() as db:
        s = bills.status(db, date(2026, 10, 9))
        [w] = bills.watch(db, date(2026, 10, 9))[0]
    assert s["late"] == [] and s["soon"] == []
    assert w.expected == date(2026, 11, 2) and bills.usual_day(w) == 2


@pytest.mark.parametrize(
    ("day", "expected"),
    [(date(2026, 10, 28), "2026-11-28"), (date(2026, 11, 1), "2026-11-28")],
)
def test_next_bill_after_one_paid_around_the_turn_of_the_month(day, expected):
    # Usually paid three days before the month ends: Nov 1 belongs with October's, so November's is next.
    assert bills.next_on_day(day, -3).isoformat() == expected
