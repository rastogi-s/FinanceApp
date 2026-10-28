from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from financeapp import budget, household, plan
from financeapp.api import plan_routes
from financeapp.models import Account, Balance, HoldingSnapshot, Paycheck, Transaction
from tests.conftest import csrf, db_of, make_client, needs_db
from tests.test_budget import TODAY, add_transactions, history

pytestmark = needs_db


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for module in (budget, household, plan_routes):
        monkeypatch.setattr(module, "today", lambda: TODAY)


def test_the_arithmetic():
    assert plan.grow(100_00, 10_00, 0, 12) == 220_00
    # $1,000 at 12% a year (1% a month) for a year, nothing added.
    assert plan.grow(1_000_00, 0, 12, 12) == round(1_000_00 * 1.01**12)
    assert plan.target_for(60_000_00, 4) == 1_500_000_00
    assert plan.months_to(100, 100, 0, 6) == 0
    assert plan.months_to(1_200_00, 0, 100_00, 0) == 12
    assert plan.months_to(10**12, 0, 1, 0) is None  # never, within 60 years


def link_balance(client, dollars: float) -> None:
    with db_of(client).session() as db:
        a = db.scalar(select(Account).where(Account.external_id == "checking"))
        db.add(Balance(account_id=a.id, date=TODAY, current_cents=round(dollars * 100)))


def pay(client, day: str, gross: float, taxes: float, savings: float, net: float) -> None:
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date.fromisoformat(day),
                gross_cents=round(gross * 100),
                taxes_cents=round(taxes * 100),
                savings_cents=round(savings * 100),
                net_cents=round(net * 100),
            )
        )


def test_projection_and_financial_independence(client, home):
    home.setup()
    assert client.get("/api/plan").json() == {"demo": False, "linked": False}
    history(client)
    link_balance(client, 100_000)
    p = client.get("/api/plan").json()
    i = p["inputs"]
    assert p["linked"] and p["net_worth_cents"] == 100_000_00
    # No income yet, so nothing to project until a savings number is typed in.
    assert i["monthly_savings_cents"] is None and p["projection"] is None
    assert i["savings_source"]["kind"] == "none"
    assert client.get("/api/household").json()["projection"] is None
    # Regular spending: the median month (June: rent $2,000, groceries $500, Netflix $15) for a year; fat is half
    # again. Every one of those is a bill or an essential, so lean is the same here.
    regular = i["levels"]["regular"]
    assert (regular["yearly_cents"], regular["typed"]) == (12 * 251_500, False)
    assert regular["note"] == "From your median month of spending, May 2026 to Sep 2026."
    assert i["levels"]["fat"]["yearly_cents"] == round(12 * 251_500 * 1.5)
    assert i["levels"]["lean"]["yearly_cents"] == 12 * 251_500
    # All cash, so the middle return is cash's.
    assert i["returns"] == {"low": 0.0, "mid": 1.0, "high": 3.0} and i["returns_source"]["mix"]["cash"] == 100

    # Paychecks for the five full months: $9,000 gross, $2,000 taxes, $1,000 to the 401(k), $6,000 net.
    for m in range(5, 10):
        pay(client, f"2026-{m:02}-15", 9_000, 2_000, 1_000, 6_000)
        add_transactions(client, [(f"2026-{m:02}-15", "Acme payroll", 6_000, "Paycheck")])
    p = client.get("/api/plan").json()
    saved = [m["saved_cents"] for m in client.get("/api/budget").json()["savings_history"]]
    assert p["inputs"]["monthly_savings_cents"] == round(sum(saved) / len(saved))
    assert p["inputs"]["savings_source"]["kind"] == "savings_rate"
    proj = p["projection"]
    assert [b["year"] for b in proj["bands"]][:2] == [2026, 2027] and len(proj["bands"]) == plan.YEARS + 1
    assert proj["bands"][0]["mid"] == 100_000_00
    assert proj["bands"][10]["low"] < proj["bands"][10]["mid"] < proj["bands"][10]["high"]
    assert proj["target_cents"] == 12 * 251_500 * 25  # at 4%
    assert proj["fire_year"]["high"] <= proj["fire_year"]["mid"] <= proj["fire_year"]["low"]
    # Home and the brief get the same projection.
    assert client.get("/api/household").json()["projection"] == proj

    # Choices: lean spending, a 3.5% withdrawal rate, a typed savings number and return, a birth year.
    h = csrf(client)
    body = {"level": "lean", "withdrawal_rate": 3.5, "savings_cents": 3_000_00, "birth_year": 1990, "return_mid": 6}
    p = client.patch("/api/plan/settings", json=body, headers=h).json()
    assert p["inputs"]["savings_typed"] and p["inputs"]["monthly_savings_cents"] == 3_000_00
    assert p["projection"]["target_cents"] == round(12 * 251_500 / 0.035)
    assert p["inputs"]["birth_year"] == 1990
    assert p["inputs"]["returns"] == {"low": 4.0, "mid": 6.0, "high": 8.0} and p["inputs"]["returns_source"]["typed"]
    # A typed spending level, then back to the ones the app works out; null goes back to measured.
    p = client.patch("/api/plan/settings", json={"spending": {"lean": 40_000_00}}, headers=h).json()
    lean = p["inputs"]["levels"]["lean"]
    assert (lean["yearly_cents"], lean["typed"], lean["derived_cents"]) == (40_000_00, True, 12 * 251_500)
    p = client.patch(
        "/api/plan/settings", json={"spending": {"lean": None}, "savings_cents": None, "return_mid": None}, headers=h
    ).json()
    assert p["inputs"]["levels"]["lean"]["typed"] is False
    assert p["inputs"]["savings_typed"] is False and p["inputs"]["birth_year"] == 1990
    assert p["inputs"]["returns_source"]["typed"] is False and p["inputs"]["returns"]["mid"] == 1.0
    assert client.patch("/api/plan/settings", json={"withdrawal_rate": 9}, headers=h).status_code == 422
    assert client.patch("/api/plan/settings", json={"level": "huge"}, headers=h).status_code == 422
    assert client.patch("/api/plan/settings", json={"return_mid": 20}, headers=h).status_code == 422


def test_savings_from_whatever_there_is(client, home):
    """Without stubs for whole months, savings still comes from the stubs there are, then deposits."""
    home.setup()
    history(client)
    link_balance(client, 10_000)
    # Take-home deposits only: what landed minus spending.
    for m in range(5, 10):
        add_transactions(client, [(f"2026-{m:02}-15", "Acme payroll", 6_000, "Paycheck")])
    s = client.get("/api/plan").json()["inputs"]
    assert s["savings_source"]["kind"] == "take_home"
    assert "Add a pay stub" in s["savings_source"]["note"]
    landed = s["monthly_savings_cents"]
    assert sum(b["cents"] for b in s["savings_source"]["breakdown"]) == landed
    # Biweekly stubs from this month: $2,800 take-home and $460 to the 401(k), 26 a year, and a bonus stub.
    # What landed stays what landed (the stubs aren't scaled up over it); the 401(k) never lands, so it's added,
    # from the typical paycheck rather than the bonus.
    with db_of(client).session() as db:
        for day, net, put in ((2, 2_800_00, 460_00), (10, 9_000_00, 2_000_00), (16, 2_800_00, 460_00)):
            db.add(
                Paycheck(
                    date=date(2026, 10, day),
                    frequency="biweekly",
                    gross_cents=net + put + 1_000_00,
                    taxes_cents=900_00,
                    savings_cents=put,
                    net_cents=net,
                )
            )
    s = client.get("/api/plan").json()["inputs"]
    src = s["savings_source"]
    assert src["kind"] == "take_home" and "401(k)" in src["short"]
    put_away = round(460_00 * 26 / 12)
    assert s["monthly_savings_cents"] == landed + put_away
    assert [b["cents"] for b in src["breakdown"] if "401(k)" in b["label"]] == [put_away]
    assert sum(b["cents"] for b in src["breakdown"]) == s["monthly_savings_cents"]


def test_savings_from_pay_stubs_alone(client, home):
    """No full month of deposits yet: the typical paycheck as an average month, minus spending so far."""
    home.setup()
    add_transactions(client, [("2026-10-05", "Landlord", -3_000, "Rent & mortgage")])
    link_balance(client, 1_000)
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date(2026, 10, 16),
                frequency="biweekly",
                gross_cents=4_200_00,
                taxes_cents=900_00,
                savings_cents=460_00,
                net_cents=2_800_00,
            )
        )
    s = client.get("/api/plan").json()["inputs"]
    src = s["savings_source"]
    assert src["kind"] == "pay_stubs"
    net, put_away, spent = round(2_800_00 * 26 / 12), round(460_00 * 26 / 12), round(3_000_00 * 30 / 90)
    assert s["monthly_savings_cents"] == net + put_away - spent
    assert "$2,800 a paycheck, paid every two weeks (26 a year)" in src["note"]
    assert [b["cents"] for b in src["breakdown"]] == [net, put_away, -spent]


def test_savings_from_the_last_90_days(client, home):
    home.setup()
    # Only this month so far: no full month yet.
    add_transactions(
        client,
        [("2026-10-02", "Acme payroll", 6_000, "Paycheck"), ("2026-10-05", "Landlord", -2_000, "Rent & mortgage")],
    )
    link_balance(client, 1_000)
    s = client.get("/api/plan").json()["inputs"]
    assert s["months_used"] == 0 and s["savings_source"]["kind"] == "recent"
    assert s["monthly_savings_cents"] == round((6_000_00 - 2_000_00) * 30 / 90)
    assert s["levels"]["regular"]["yearly_cents"] == round(2_000_00 * 30 / 90) * 12


def test_return_from_the_mix(client, home):
    home.setup()
    history(client)
    link_balance(client, 20_000)  # cash
    with db_of(client).session() as db:
        checking = db.scalar(select(Account).where(Account.external_id == "checking"))
        broker = Account(
            connection_id=checking.connection_id,
            external_id="broker",
            name="Brokerage",
            institution="Bank",
            type="investment",
        )
        db.add(broker)
        db.flush()
        db.add(Balance(account_id=broker.id, date=TODAY, current_cents=80_000_00))
        for symbol, cls, dollars in (("AAPL", "Stocks", 60_000), ("BND", "Bonds", 20_000)):
            db.add(
                HoldingSnapshot(
                    date=TODAY,
                    account_id=broker.id,
                    symbol=symbol,
                    name=symbol,
                    asset_class=cls,
                    quantity=Decimal(100),
                    price=Decimal(dollars) / 100,
                    value_cents=dollars * 100,
                )
            )
    i = client.get("/api/plan").json()["inputs"]
    # 60% stocks at 6.5%, 20% bonds at 2%, 20% cash at 0.5%: 4.4%, to the nearest half point.
    assert i["returns_source"]["mix"] == {"stocks": 60, "bonds": 20, "cash": 20, "other": 0}
    assert i["returns"] == {"low": 2.5, "mid": 4.5, "high": 6.5}
    assert i["returns_source"]["note"].startswith("From your mix today: 60% stocks, 20% bonds, 20% cash.")


def test_subscriptions_and_rising_categories(client, home):
    home.setup()
    rows = [("2026-06-01", "Corner shop", -5, "Shopping")]  # June 1st on: June to September are full months
    for m in range(6, 11):
        rows.append((f"2026-{m:02}-05", "NETFLIX.COM", -15.49 if m < 10 else -17.99, "Subscriptions"))
        rows.append((f"2026-{m:02}-09", "Gym", -40, "Subscriptions"))
        # Coffee every week: regular, but not a subscription.
        rows += [(f"2026-{m:02}-{d:02}", "Blue Bottle", -6, "Dining out") for d in (2, 9, 16, 23)]
    rows += [("2026-07-01", "Hardware store", -30, "Shopping"), ("2026-09-01", "Hardware store", -31, "Shopping")]
    # Dining out: about $100 a month in June to August (with the coffee), then $260 in September.
    rows += [(f"2026-{m:02}-20", "Bistro", -76, "Dining out") for m in (6, 7, 8)]
    rows += [("2026-09-20", "Bistro", -236, "Dining out")]
    add_transactions(client, rows)
    ideas = client.get("/api/plan").json()["ideas"]
    subs = {s["merchant"]: s for s in ideas["subscriptions"]}
    assert set(subs) == {"NETFLIX.COM", "Gym"}
    assert subs["Gym"]["monthly_cents"] == 40_00 and subs["Gym"]["yearly_cents"] == 480_00
    assert subs["NETFLIX.COM"]["went_up"] is True and subs["Gym"]["went_up"] is False
    assert ideas["subscriptions_yearly_cents"] == sum(s["yearly_cents"] for s in subs.values())
    rising = {r["category"]: r for r in ideas["rising"]}
    assert set(rising) == {"Dining out"}
    assert rising["Dining out"]["month"] == "2026-09-01"
    assert rising["Dining out"]["change_cents"] == 260_00 - 100_00


def test_setup_guide(client, home, monkeypatch):
    home.setup()
    g = client.get("/api/guide").json()
    steps = {s["id"]: s for s in g["steps"]}
    assert [s["id"] for s in g["steps"]][:4] == ["passkey", "accounts", "budget", "alerts"]
    assert g["needed"] == 4 and g["needed_done"] == 0 and not g["complete"] and not g["hidden"]
    assert steps["budget"]["waiting"] == "Link an account first."
    assert steps["alerts"]["waiting"] and steps["alerts"]["test"] is False  # no channel on this server
    assert steps["people"]["optional"] and steps["ai"]["optional"]

    history(client)
    h = csrf(client)
    assert client.post("/api/budget/draft", headers=h).status_code == 200
    client.patch("/api/plan/settings", json={"birth_year": 1988}, headers=h)
    steps = {s["id"]: s for s in client.get("/api/guide").json()["steps"]}
    assert steps["accounts"]["done"] and steps["budget"]["done"] and steps["budget"]["waiting"] is None
    assert steps["plan"]["done"] and not steps["paycheck"]["done"]

    g = client.put("/api/guide", json={"hidden": True}, headers=h).json()
    assert g["hidden"] is True
    assert client.get("/api/guide").json()["hidden"] is True


def test_demo_plan_and_guide(tmp_path):
    with make_client(tmp_path, "", demo_mode=True) as c:
        p = c.get("/api/plan").json()
        assert p["demo"] and p["projection"]["fire_year"]["mid"] is not None
        assert p["projection"] == c.get("/api/household").json()["projection"]
        assert p["ideas"]["subscriptions"] and p["ideas"]["rising"]
        assert c.get("/api/guide").json()["complete"] is True
        assert c.patch("/api/plan/settings", json={"level": "lean"}).status_code == 403
        assert c.put("/api/guide", json={"hidden": True}).status_code == 403


def add_to(client, external_id: str, rows: list[tuple[str, str, float, str | None]], kind: str = "credit") -> None:
    """Like add_transactions, into another account (made on first use)."""
    import uuid

    from financeapp.models import Category, Connection, Transaction

    with db_of(client).session() as db:
        account = db.scalar(select(Account).where(Account.external_id == external_id))
        if account is None:
            conn = Connection(provider="manual", institution="Card co", state={})
            db.add(conn)
            db.flush()
            account = Account(
                connection_id=conn.id, external_id=external_id, name=external_id, institution="Card co", type=kind
            )
            db.add(account)
            db.flush()
        categories = {c.name: c.id for c in db.scalars(select(Category))}
        for day, merchant, dollars, category in rows:
            db.add(
                Transaction(
                    account_id=account.id,
                    external_id=uuid.uuid4().hex,
                    date=date.fromisoformat(day),
                    amount_cents=round(dollars * 100),
                    merchant=merchant,
                    description=merchant,
                    category_id=categories[category] if category else None,
                    category_source="user" if category else "none",
                    source="manual",
                )
            )


def test_savings_stitches_accounts_and_leaves_out_transfers(client, home):
    """The card has history from April, checking only from August: only months both cover count. A paycheck that
    landed uncategorized still counts as pay once a stub matches it, and money moved from savings isn't income."""
    home.setup()
    card = [(f"2026-{m:02}-{d:02}", "Grocer", -50, "Groceries") for m in range(4, 10) for d in range(1, 25)]
    add_to(client, "card", card)
    rows = []
    for m in (8, 9):
        rows += [(f"2026-{m:02}-{d:02}", "Coffee", -5, "Dining out") for d in range(1, 21)]
        rows += [(f"2026-{m:02}-01", "ACME CORP", 3_500, None), (f"2026-{m:02}-15", "ACME CORP", 3_500, None)]
    rows.append(("2026-10-01", "ACME CORP", 3_500, None))
    add_transactions(client, rows)
    add_transactions(client, [("2026-09-20", "From savings", 5_000, "Transfer")], transfer=True)
    link_balance(client, 1_000)
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date(2026, 10, 1),
                frequency="semimonthly",
                gross_cents=5_000_00,
                taxes_cents=1_000_00,
                savings_cents=500_00,
                net_cents=3_500_00,
            )
        )
    s = client.get("/api/plan").json()["inputs"]
    src = s["savings_source"]
    assert [r["month"] for r in src["months"]] == ["2026-08-01", "2026-09-01"]
    assert src["kind"] == "take_home"
    lines = {b["label"]: b["cents"] for b in src["breakdown"]}
    assert lines["Pay landing in your accounts"] == 7_000_00
    assert lines["Spending"] == -(24 * 50_00 + 20 * 5_00)
    assert lines["Into your 401(k), HSA or ESPP before pay, from your pay stubs"] == 500_00 * 2
    assert not any("Other money" in k for k in lines)
    assert src["excluded"]["cents"] == 5_000_00 // 2
    assert s["monthly_savings_cents"] == sum(lines.values())


def test_savings_rate_breakdown_agrees_with_plan(client, home):
    """Budget's savings rate counts the same pay, other money in and spending as Plan's month rows, and says where
    the savings went: into a linked savings account, a brokerage, cards that aren't linked, and elsewhere."""
    from financeapp import transfers

    home.setup()
    history(client)
    add_transactions(
        client,
        [
            ("2026-08-15", "ACME CORP", 3_000, "Paycheck"),
            # Pay that landed uncategorized: still pay (it used to count as a $6,000 refund against spending).
            ("2026-09-01", "ACME CORP", 3_000, None),
            ("2026-09-15", "ACME CORP", 3_000, None),
            ("2026-09-30", "Bank interest", 10, "Interest & dividends"),
            ("2026-09-12", "Zelle from Mom", 900, "Other income"),
            ("2026-09-16", "FIDELITY MONEYLINE", -1_000, "Transfer"),
            ("2026-09-20", "CHASE CREDIT CRD AUTOPAY", -400, "Transfer"),
            ("2026-09-22", "Zelle to Sam", -300, "Transfer"),
            ("2026-09-18", "Move to savings", -500, None),
        ],
    )
    add_to(client, "savings", [("2026-09-18", "From checking", 500, None)], kind="depository")
    with db_of(client).session() as db:
        db.scalar(select(Account).where(Account.external_id == "savings")).subtype = "savings"
        assert transfers.match_transfers(db, date(2026, 9, 1)) == 1

    b = client.get("/api/budget").json()
    rate = b["savings_rate"]
    assert rate["month"] == "2026-09-01" and rate["basis"] == "take_home"
    # $6,000 pay + $10 interest + $900 other in, $2,000 rent + $550 groceries spent.
    assert rate["income_cents"] == 691_000 and rate["spending_cents"] == 255_000 and rate["saved_cents"] == 436_000
    assert rate["percent"] == 63.1
    bd = rate["breakdown"]
    assert {x["label"]: x["cents"] for x in bd["money_in"]} == {
        "Pay (take-home)": 600_000,
        "Other money in": 90_000,
        "Interest & dividends": 1_000,
    }
    out = {x["label"]: x for x in bd["money_out"]}
    assert out["Bills"]["cents"] == 200_000 and out["Everyday"]["items"] == [{"label": "Groceries", "cents": 55_000}]
    assert sum(x["cents"] for x in bd["money_out"]) == rate["spending_cents"]
    went = {x["label"]: x["cents"] for x in bd["went"]}
    assert went == {
        "Into savings": 50_000,
        "Into Fidelity": 100_000,
        "Paid to cards that aren't linked": 40_000,
        "Sent to accounts that aren't linked": 30_000,
        "Stayed in your everyday accounts": 436_000 - 220_000,
    }
    assert sum(went.values()) == rate["saved_cents"]
    assert [n["kind"] for n in bd["notes"]] == ["take_home", "unlinked_cards", "other_transfers", "other_income"]

    # Plan's September row is the same money.
    rows = {r["month"]: r for r in client.get("/api/plan").json()["inputs"]["savings_source"]["months"]}
    sep = rows["2026-09-01"]
    assert sep["pay_cents"] + sep["other_in_cents"] == rate["income_cents"]
    assert sep["spending_cents"] + sep["taxes_cents"] == rate["spending_cents"] + rate["taxes_cents"]


def test_utilities_are_not_subscriptions(client, home):
    home.setup()
    rows = [("2026-06-01", "Corner shop", -5, "Shopping")]
    power = {6: -150, 7: -210, 8: -230, 9: -190, 10: -183.07}  # moves with the weather
    for m in range(6, 11):
        rows.append((f"2026-{m:02}-05", "Spotify", -11.99, "Subscriptions"))
        rows.append((f"2026-{m:02}-11", "Duke Energy", power[m], "Utilities"))
        rows.append((f"2026-{m:02}-14", "City of Durham", -96.55 if m == 10 else -88.10, "Shopping"))
        rows.append((f"2026-{m:02}-20", "Verizon", -70, "Subscriptions"))
        rows.append((f"2026-{m:02}-01", "State Farm", -120, "Insurance"))
    add_transactions(client, rows)
    ideas = client.get("/api/plan").json()["ideas"]
    assert [s["merchant"] for s in ideas["subscriptions"]] == ["Spotify"]
    assert ideas["subscriptions_yearly_cents"] == 1199 * 12
    bills = {u["merchant"]: u for u in ideas["utilities"]}
    assert set(bills) == {"Duke Energy", "City of Durham", "Verizon", "State Farm"}
    assert bills["City of Durham"]["kind"] == "utility" and bills["City of Durham"]["went_up"] is True
    assert bills["Verizon"]["kind"] == "utility" and bills["State Farm"]["kind"] == "bill"
    duke = bills["Duke Energy"]
    assert duke["went_up"] is False and duke["higher"] is False and duke["usual_cents"] == 200_00


def test_only_payroll_counts_as_pay(client, home):
    """Pay is the employer's payroll deposits, however each bank words them; Zelle, reimbursements and money from
    the household's own name are not pay, and anyone can be marked pay, other money in, or the household's own."""
    from financeapp import transfers

    home.setup(name="Shubham Rastogi")
    history(client)
    rows = []
    for m in (7, 8, 9):
        rows += [
            (f"2026-{m:02}-01", "Deposit from NETAPP INC PAYROLL", 3_000, None),
            (f"2026-{m:02}-15", "NETAPP INC DIR DEP PPD 99812", 3_000, "Other income"),  # another bank's words
        ]
    rows += [
        ("2026-09-20", "NETAPP INC", 500, None),  # no payroll words, but the employer's name
        ("2026-09-21", "NETAPP INC EXPENSE REIMB", 120, "Other income"),
        ("2026-09-12", "Zelle payment from JANE DOE conf 1234", 900, "Other income"),
        ("2026-09-13", "Zelle payment from SHUBHAM RASTOGI conf 77", 2_000, "Other income"),
        ("2026-09-14", "VENMO CASHOUT", 300, "Other income"),
    ]
    add_transactions(client, rows)
    with db_of(client).session() as db:
        # These came in by hand-picked category; a sync's guesses are what get re-sorted.
        for t in db.scalars(
            select(Transaction).where(Transaction.merchant.ilike("%zelle%") | Transaction.merchant.ilike("%venmo%"))
        ):
            t.category_source = "provider"
        transfers.match_transfers(db, date(2026, 6, 1))
    link_balance(client, 1_000)

    def september(p):
        return next(r for r in p["inputs"]["savings_source"]["months"] if r["month"] == "2026-09-01")

    p = client.get("/api/plan").json()
    src = p["inputs"]["savings_source"]
    sep = september(p)
    assert sep["pay_cents"] == 6_500_00 and sep["pay_deposits"] == 3
    assert sep["other_in_cents"] == 120_00 + 900_00  # the reimbursement and Jane; not Shubham, not the cash-out
    assert "NETAPP" in src["breakdown"][0]["detail"]
    who = {(m["key"], m["as"]) for m in src["money_in"]}
    assert ("netapp", "pay") in who and ("jane doe", "other") in who and ("shubham rastogi", "me") in who

    # Jane turns out to be the household's own other account; then back.
    h = csrf(client)
    p = client.put("/api/plan/money-in", json={"key": "jane doe", "counts_as": "me"}, headers=h).json()
    assert september(p)["other_in_cents"] == 120_00
    p = client.put("/api/plan/money-in", json={"key": "JANE DOE", "counts_as": "other"}, headers=h).json()
    assert september(p)["other_in_cents"] == 120_00 + 900_00
    # NetApp isn't pay after all (say it's a pension): it all moves to nothing counted as pay.
    p = client.put("/api/plan/money-in", json={"key": "netapp", "counts_as": "other"}, headers=h).json()
    assert september(p)["pay_cents"] == 0
    p = client.put("/api/plan/money-in", json={"key": "netapp", "counts_as": "pay"}, headers=h).json()
    assert september(p)["pay_cents"] == 6_500_00
    assert client.put("/api/plan/money-in", json={"key": "!!", "counts_as": "pay"}, headers=h).status_code == 422


@needs_db
def test_savings_rate_sources_bonus_third_paycheck_and_etrade(client, home):
    """Pay broken down by employer and paychecks; a bonus and a third two-weekly paycheck kept out of the usual
    month; withholding filled in from the usual stub for paychecks without one; transfers to E*TRADE invested."""
    from datetime import timedelta

    from financeapp import transfers

    home.setup()
    history(client)
    rows = []
    day = date(2026, 5, 8)
    while day < date(2026, 10, 21):  # every two weeks: July has three
        rows.append((day.isoformat(), "Deposit from NETAPP INC PAYROLL", 3_000, None))
        day += timedelta(days=14)
    rows += [
        ("2026-08-20", "NETAPP INC PAYROLL", 9_000, None),  # a bonus
        ("2026-09-10", "MORGAN STANLEY ACH", -1_000, "Transfer"),
        ("2026-09-11", "Online transfer to brokerage xxxx2600", -500, "Transfer"),
        ("2026-09-12", "Zelle from Mom", 200, "Other income"),
    ]
    add_transactions(client, rows)
    add_to(client, "etrade", [("2026-09-01", "Dividend", 1, None)], kind="investment")
    with db_of(client).session() as db:
        a = db.scalar(select(Account).where(Account.external_id == "etrade"))
        a.mask, a.name = "2600", "E*TRADE Individual"
        db.add(
            Paycheck(
                date=date(2026, 10, 9),
                frequency="biweekly",
                gross_cents=4_500_00,
                taxes_cents=900_00,
                savings_cents=400_00,
                net_cents=3_000_00,
            )
        )
        transfers.match_transfers(db, date(2026, 5, 1))
    months = {m["month"]: m for m in client.get("/api/budget").json()["savings_history"]}

    jul = months["2026-07-01"]
    assert jul["basis"] == "gross" and jul["estimated_paychecks"] == 3
    money_in = {x["label"]: x for x in jul["breakdown"]["money_in"]}
    pay = money_in["Pay before taxes, 3 paychecks without a stub (from your usual stub)"]
    assert pay["cents"] == 3 * 4_500_00 and pay["items"] == [{"label": "NETAPP: 3 paychecks", "cents": 9_000_00}]
    taxes = next(x for x in jul["breakdown"]["money_out"] if x["label"] == "Taxes")
    assert taxes["items"] == [{"label": "Withheld, estimated from your usual stub", "cents": 3 * 900_00}]
    assert jul["usual"]["extra_paychecks"] == 1 and jul["usual"]["percent"] < jul["percent"]

    aug = months["2026-08-01"]
    labels = [x["label"] for x in aug["breakdown"]["money_in"]]
    assert "Bonus (take-home), left out of your usual month" in labels
    assert aug["usual"]["bonus_cents"] == 9_000_00 and aug["usual"]["saved_cents"] == aug["saved_cents"] - 9_000_00

    sep = months["2026-09-01"]
    assert sep["usual"] is None  # two paychecks, no bonus
    went = {x["label"]: x["cents"] for x in sep["breakdown"]["went"]}
    assert went["Into E*TRADE"] == 1_000_00 and went["Into E*TRADE Individual"] == 500_00
    other = next(x for x in sep["breakdown"]["money_in"] if x["label"] == "Other money in")
    assert other["items"] == [{"label": "Zelle from Mom", "cents": 200_00}]

    # Plan's usual month leaves the bonus out.
    rows = {r["month"]: r for r in client.get("/api/plan").json()["inputs"]["savings_source"]["months"]}
    assert rows["2026-08-01"]["pay_cents"] == 2 * 3_000_00


@needs_db
def test_split_paycheck_counts_once_and_a_changed_paycheck_asks_for_its_stub(client, home, monkeypatch):
    """A paycheck split between checking and savings is one paycheck, its stub matches the two deposits together,
    and a paycheck that comes in different from the one before (with no stub yet) asks for its stub, once."""
    from datetime import timedelta

    from financeapp import notify

    sent = []
    monkeypatch.setattr(notify, "send", lambda settings, title, body, tags="", link=None: sent.append((title, body)))
    home.setup()
    history(client)
    checking, savings = [], []
    day = date(2026, 5, 8)
    while day < date(2026, 10, 21):
        raise_ = day >= date(2026, 10, 9)  # a raise on the last paycheck
        checking.append((day.isoformat(), "NETAPP INC DIR DEP", 3_300 if raise_ else 3_000, None))
        savings.append(((day + timedelta(days=1)).isoformat(), "NETAPP INC PAYROLL", 500, None))
        day += timedelta(days=14)
    add_transactions(client, checking)
    add_to(client, "savings", savings, kind="depository")
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date(2026, 9, 25),
                frequency="biweekly",
                gross_cents=5_000_00,
                taxes_cents=1_000_00,
                savings_cents=500_00,
                net_cents=3_500_00,  # 3,000 to checking and 500 to savings
            )
        )
    state = client.get("/api/budget").json()
    sep = {m["month"]: m for m in state["savings_history"]}["2026-09-01"]
    pay = sep["breakdown"]["money_in"][0]
    assert pay["items"] == [{"label": "NETAPP: 2 paychecks", "cents": 7_000_00}]
    assert sep["usual"] is None  # two split paychecks are two paychecks, not four
    assert state["paychecks"][0]["deposit"] is not None  # the stub matched checking and savings together

    assert state["stub_requests"] == [
        {"date": "2026-10-09", "employer": "NETAPP", "cents": 3_800_00, "before_cents": 3_500_00, "reason": "changed"}
    ]
    with db_of(client).session() as db:
        assert budget.ask_for_stubs(db, client.app.state.settings) == 1
        assert budget.ask_for_stubs(db, client.app.state.settings) == 0  # once
    assert sent[0][0] == "Add this pay stub" and "Oct 9" in sent[0][1]

    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date(2026, 10, 9),
                frequency="biweekly",
                gross_cents=5_400_00,
                taxes_cents=1_100_00,
                savings_cents=500_00,
                net_cents=3_800_00,
            )
        )
    assert client.get("/api/budget").json()["stub_requests"] == []


def test_money_into_etrade_is_investing(client, home):
    """A bank withdrawal whose wording names no brokerage is paired with E*TRADE's own deposit record, so it counts
    as money moved into investing (not spending), and Budget says it went into that E*TRADE account."""
    from financeapp import transfers
    from financeapp.models import Connection, InvestmentTransaction

    home.setup()
    history(client)
    add_transactions(
        client,
        [
            ("2026-09-01", "ACME CORP", 6_000, "Paycheck"),
            ("2026-09-08", "ONLINE ACH DEBIT 0042", -1_500, None),  # into E*TRADE, the bank doesn't say so
            ("2026-09-20", "ACH DEBIT WEB", -700, None),  # no deposit of this amount anywhere: stays spending
            ("2026-09-25", "ACH CREDIT", 400, None),  # taken out of E*TRADE
            ("2026-09-28", "Pending ACH", -900, None),
        ],
    )
    with db_of(client).session() as db:
        db.scalar(select(Transaction).where(Transaction.merchant == "Pending ACH")).pending = True
        conn = Connection(provider="plaid", institution="E*TRADE", state={})
        db.add(conn)
        db.flush()
        etrade = Account(
            connection_id=conn.id, external_id="et", name="Brokerage", institution="E*TRADE", type="investment",
            mask="2600",
        )  # fmt: skip
        db.add(etrade)
        db.flush()
        for n, (day, kind, subtype, cents, symbol) in enumerate(
            [
                ("2026-09-10", "cash", "deposit", 1_500_00, None),
                ("2026-09-24", "transfer", "transfer", -400_00, None),
                ("2026-09-28", "cash", "deposit", 900_00, None),  # the bank side is still pending
                ("2026-09-12", "buy", "buy", -1_500_00, "VTI"),  # buying with it isn't a move
            ]
        ):
            db.add(
                InvestmentTransaction(
                    account_id=etrade.id, external_id=f"e{n}", date=date.fromisoformat(day), type=kind,
                    subtype=subtype, symbol=symbol, name="", amount_cents=cents, source="plaid",
                )
            )  # fmt: skip
        db.flush()
        assert transfers.match_transfers(db, date(2026, 9, 1)) == 2
        assert transfers.match_transfers(db, date(2026, 9, 1)) == 0  # already paired
        paired = {
            t.merchant: t for t in db.scalars(select(Transaction).where(Transaction.transfer_group_id.is_not(None)))
        }
        assert {"ONLINE ACH DEBIT 0042", "ACH CREDIT"} <= set(paired)
        assert "ACH DEBIT WEB" not in paired and "Pending ACH" not in paired

    rate = client.get("/api/budget").json()["savings_rate"]
    # $6,000 pay; $2,000 rent + $550 groceries + the unexplained $700 and the still-pending $900 spent. The $400
    # back from E*TRADE isn't income.
    assert rate["income_cents"] == 600_000 and rate["spending_cents"] == 415_000
    went = {x["label"]: x for x in rate["breakdown"]["went"]}
    assert went["Into E*TRADE Brokerage ••2600"]["cents"] == 1_100_00
    assert went["Into E*TRADE Brokerage ••2600"]["kind"] == "invest"
    assert sum(x["cents"] for x in went.values()) == rate["saved_cents"]


def test_brokerage_wording_spots_more_etrade_transfers():
    from financeapp.transfers import ONE_SIDED

    for text in ("E TRADE SECURITIES ACH", "ETRADE TRANSFER", "MORGAN STANLEY ACH DEPOSIT"):
        assert ONE_SIDED.search(text), text
    assert not ONE_SIDED.search("The Trade Desk deposit")


@needs_db
def test_one_paycheck_split_four_ways_and_worded_four_ways(client, home):
    """ADP pays take-home into four accounts. Each bank words its deposit its own way ("Early Paycheck", a payroll
    code, or nothing at all), and one even lands two days early. It's still one NETAPP paycheck each payday."""
    from datetime import timedelta

    home.setup()
    history(client)
    checking, boa, small, saving = [], [], [], []
    day = date(2026, 5, 8)
    while day < date(2026, 10, 21):
        checking.append(((day - timedelta(days=2)).isoformat(), "Early Paycheck NETAPP INC PAYROLL", 3_084.55, None))
        boa.append((day.isoformat(), "NETAPP INC DES:DIR DEP ID:0042 INDN:RASTOGI", 395.45, None))
        small.append((day.isoformat(), "NETAPP INC", 237.27, None))
        saving.append((day.isoformat(), "ONLINE DEPOSIT", 237.27, None))  # says nothing about pay
        day += timedelta(days=14)
    add_transactions(client, checking)
    add_to(client, "boa", boa, kind="depository")
    add_to(client, "small", small, kind="depository")
    add_to(client, "saving", saving, kind="depository")
    with db_of(client).session() as db:
        for external_id, mask in (("boa", "7006"), ("small", "3976"), ("saving", "9933")):
            db.scalar(select(Account).where(Account.external_id == external_id)).mask = mask
        splits = [
            {"mask": "3976", "cents": 237_27},
            {"mask": "0115", "cents": 3_084_55},
            {"mask": "7006", "cents": 395_45},
            {"mask": "9933", "cents": 237_27},
        ]
        db.add(
            Paycheck(
                date=date(2026, 10, 16),
                frequency="biweekly",
                gross_cents=7_773_08,
                taxes_cents=1_662_15,
                savings_cents=1_893_47,
                net_cents=3_954_54,
                detail={"deposits": splits},
            )
        )
    state = client.get("/api/budget").json()
    sep = {m["month"]: m for m in state["savings_history"]}["2026-09-01"]
    assert sep["breakdown"]["money_in"][0]["items"] == [{"label": "NETAPP: 2 paychecks", "cents": 2 * 3_954_54}]
    assert sep["usual"] is None and sep["estimated_paychecks"] == 2
    assert state["stub_requests"] == []  # every paycheck the same

    p = client.get("/api/plan").json()["inputs"]
    pay = p["savings_source"]["breakdown"][0]
    assert pay["detail"].startswith("2") and pay["detail"].endswith("paychecks a month from NETAPP")
    rows = [r for r in p["savings_source"]["money_in"] if r["as"] == "pay"]
    months = p["savings_source"]["months"]
    assert [r["key"] for r in rows] == ["netapp"]
    assert rows[0]["count"] == sum(m["pay_deposits"] for m in months)  # paydays, not four deposits each
    assert not [r for r in p["savings_source"]["money_in"] if r["key"] == "online deposit"]


@needs_db
def test_two_people_paid_on_the_same_days_stay_two_employers(client, home):
    from datetime import timedelta

    home.setup()
    history(client)
    rows = []
    day = date(2026, 5, 8)
    while day < date(2026, 10, 21):
        rows.append((day.isoformat(), "NETAPP INC PAYROLL", 3_000, None))
        rows.append((day.isoformat(), "ACME CORP DIR DEP", 2_000, None))  # into the same joint account
        day += timedelta(days=14)
    add_transactions(client, rows)
    sep = {m["month"]: m for m in client.get("/api/budget").json()["savings_history"]}["2026-09-01"]
    assert sep["breakdown"]["money_in"][0]["items"] == [
        {"label": "NETAPP: 2 paychecks", "cents": 6_000_00},
        {"label": "ACME: 2 paychecks", "cents": 4_000_00},
    ]
