from datetime import date, timedelta

from financeapp import offers, upcoming
from financeapp.models import CardStatement, Paycheck
from tests.conftest import csrf, needs_db

TODAY = date(2026, 10, 28)
END = TODAY + timedelta(days=upcoming.DAYS)


def test_paydays_roll_forward_from_the_last_stub():
    stubs = [
        Paycheck(user_id=1, frequency="biweekly", date=date(2026, 10, 2), net_cents=2_600_00),
        Paycheck(user_id=1, frequency="biweekly", date=date(2026, 9, 18), net_cents=2_500_00),  # older: ignored
        Paycheck(user_id=1, frequency=None, date=date(2026, 10, 20), net_cents=900_00),  # a bonus stub, no schedule
    ]
    [pay] = upcoming.paydays(stubs, {1: "Sam"}, TODAY)
    # Oct 2 + 14 = Oct 16 (gone by), + 14 = Oct 30.
    assert pay["date"] == "2026-10-30" and pay["cents"] == 2_600_00 and pay["title"] == "Paycheck"
    assert pay["detail"].startswith("Every two weeks")

    # Two people paid: each paycheck says whose.
    stubs.append(Paycheck(user_id=2, frequency="semimonthly", date=date(2026, 10, 15), net_cents=3_000_00))
    rows = {p["title"]: p for p in upcoming.paydays(stubs, {1: "Sam", 2: "Alex"}, TODAY)}
    assert rows["Alex's paycheck"]["date"] == "2026-10-31" and "Sam's paycheck" in rows


def test_card_dues_only_within_the_window_and_with_a_balance():
    def due(days, cents, minimum=None):
        return CardStatement(due_date=TODAY + timedelta(days=days), statement_cents=cents, minimum_cents=minimum)

    rows = upcoming.card_dues(
        [
            ("Amex Gold ••1008", due(6, 2_140_00, 40_00)),
            ("Sapphire ••9012", due(30, 500_00)),  # too far
            ("Freedom ••1111", due(3, 0)),  # nothing owed
            ("Venture ••2222", due(-1, 80_00)),  # past
        ],
        TODAY,
        END,
    )
    assert [(r["title"], r["cents"], r["detail"]) for r in rows] == [
        ("Amex Gold ••1008 due", -2_140_00, "Statement balance, $40 minimum")
    ]


def test_dividends_and_offers_in_their_windows():
    expected = [
        {"symbol": "BND", "pay_date": "2026-11-02", "cents": 118_00, "reinvesting": True},
        {"symbol": "VTI", "pay_date": "2026-12-22", "cents": 412_00, "reinvesting": False},
    ]
    [d] = upcoming.dividends(expected, TODAY, END)
    assert d["title"] == "BND dividend" and d["detail"] == "Expected, reinvested"

    def offer(i, ends, added):
        return offers.Offer(f"o{i}", 2, "chase", f"Store {i}", f"store{i}", "percent", 5, expires=ends, added=added)

    rows = upcoming.ending_offers(
        [offer(1, TODAY + timedelta(days=2), False), offer(2, TODAY + timedelta(days=20), True), offer(3, None, True)],
        {2: "Sapphire ••9012"},
        TODAY,
    )
    assert [(r["title"], r["detail"]) for r in rows] == [
        ("Store 1 offer ends", "5% back with Sapphire ••9012, not added to the card yet")
    ]


def test_merge_is_by_date_then_kind():
    a = upcoming.event(TODAY, "offer", "B", "", None, None)
    b = upcoming.event(TODAY, "pay", "A", "", 1, None)
    c = upcoming.event(TODAY - timedelta(days=1), "dividend", "C", "", 1, None)
    assert [r["title"] for r in upcoming.merge([a], [b, c])] == ["C", "A", "B"]


@needs_db
def test_coming_up_from_real_accounts(client, home):
    home.setup()
    cd = client.post(
        "/api/accounts/manual",
        json={"name": "Ally CD", "type": "depository", "balance_cents": 10_000_00},
        headers=csrf(client),
    ).json()["accounts"][0]
    maturity = date.today() + timedelta(days=9)
    client.put(
        f"/api/accounts/{cd['id']}/terms",
        json={"apy": 4.0, "maturity": maturity.isoformat(), "term_months": 12, "auto_renew": True},
        headers=csrf(client),
    )
    [row] = client.get("/api/upcoming").json()["events"]
    assert row["kind"] == "cd" and row["date"] == maturity.isoformat() and row["href"].endswith(str(cd["id"]))
