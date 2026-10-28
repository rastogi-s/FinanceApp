from datetime import date, timedelta

from sqlalchemy import select, update

from financeapp import alerts, cardpay, upcoming
from financeapp.models import AlertEvent, Balance, CardStatement, Category, Transaction
from tests.conftest import db_of, needs_db
from tests.test_cards import credit_card
from tests.test_reminders import sent  # noqa: F401 (a fixture)

TODAY = date(2026, 10, 28)


def statement(due_in: int, cents: int, **kw) -> CardStatement:
    return CardStatement(
        due_date=TODAY + timedelta(days=due_in), statement_date=TODAY - timedelta(days=20), statement_cents=cents, **kw
    )


def test_paid_part_paid_and_not_paid():
    s = statement(8, 4_478_00)
    assert cardpay.work_out(s, 0, None, TODAY).state == "due"
    p = cardpay.work_out(s, 2_000_00, None, TODAY)
    assert (p.state, p.paid_cents, p.left_cents) == ("partial", 2_000_00, 2_478_00)
    p = cardpay.work_out(s, 4_478_00, None, TODAY)
    assert (p.state, p.left_cents) == ("paid", 0)
    # Paying more than the statement (new purchases too) is still paid in full.
    assert cardpay.work_out(s, 5_000_00, None, TODAY).state == "paid"
    # Past the due date with some left.
    assert cardpay.work_out(statement(-1, 100_00), 0, None, TODAY).state == "late"
    # Nothing owed, or no due date: nothing to say.
    assert cardpay.work_out(statement(8, 0), 0, None, TODAY) is None


def test_the_banks_last_payment_and_the_balance_count_too():
    # The bank says a payment came in after the statement, before it showed as a transaction.
    s = statement(8, 4_478_00, last_payment_cents=4_478_00, last_payment_date=TODAY - timedelta(days=1))
    assert cardpay.work_out(s, 0, None, TODAY).state == "paid"
    # One from before the statement doesn't count.
    s = statement(8, 4_478_00, last_payment_cents=3_000_00, last_payment_date=TODAY - timedelta(days=25))
    assert cardpay.work_out(s, 0, None, TODAY).state == "due"
    # Owing less now than what's left: paid down to that.
    p = cardpay.work_out(statement(8, 4_478_00), 0, 1_000_00, TODAY)
    assert p.left_cents == 1_000_00
    assert cardpay.work_out(statement(8, 4_478_00), 0, 0, TODAY).state == "paid"


def test_push_steps_and_wording_never_name_an_amount():
    def at(days):
        return cardpay.step(cardpay.work_out(statement(days, 500_00), 0, None, TODAY), TODAY)

    assert [at(d) for d in (5, 3, 1, 0, -1, -3, -4)] == [None, "soon", "soon", "today", "late", "late", None]
    assert cardpay.step(cardpay.work_out(statement(1, 500_00), 500_00, None, TODAY), TODAY) is None  # paid

    p = cardpay.work_out(statement(1, 4_478_00), 0, None, TODAY)
    title, body = cardpay.wording("Venture X ••8868", p, "soon", TODAY)
    assert title == "Venture X ••8868 payment due tomorrow" and "$" not in title + body
    p = cardpay.work_out(statement(3, 4_478_00), 1_000_00, None, TODAY)
    title, body = cardpay.wording("Venture X ••8868", p, "soon", TODAY)
    assert title == "Venture X ••8868 payment due Saturday" and body.startswith("Part of the statement is paid")


def test_coming_up_says_paid_or_whats_left():
    s1, s2, s3 = statement(8, 4_478_00, minimum_cents=40_00), statement(4, 900_00), statement(2, 300_00)
    payments = {
        1: cardpay.work_out(s1, 1_000_00, None, TODAY),
        2: cardpay.work_out(s2, 900_00, None, TODAY),
        3: cardpay.work_out(s3, 0, None, TODAY),
    }
    end = TODAY + timedelta(days=upcoming.DAYS)
    rows = upcoming.card_dues([(1, "Venture X ••8868", s1), (2, "Gold ••1008", s2), (3, "Freedom ••1111", s3)],
                              payments, TODAY, end)  # fmt: skip
    got = {r["title"]: (r["cents"], r["detail"], r["payment"]["state"]) for r in rows}
    assert got == {
        "Venture X ••8868 due": (-3_478_00, "Part paid, the rest is left", "partial"),
        "Gold ••1008 paid": (None, "Statement paid in full", "paid"),
        "Freedom ••1111 due": (-300_00, "Statement balance, not paid yet", "due"),
    }
    assert rows[0]["href"].startswith("/cards?card=") and rows[0]["payment"]["minimum_cents"] in (None, 40_00)


@needs_db
def test_payments_on_the_card_and_the_push(client, home, sent):  # noqa: F811
    home.setup()
    today = date.today()
    card = credit_card(client, "Venture X", "Capital One", "8868", 5_200)
    with db_of(client).session() as db:
        db.execute(update(Balance).where(Balance.account_id == card).values(date=today))
        due = today + timedelta(days=2)
        db.add(CardStatement(account_id=card, as_of=today, aprs=[], statement_cents=4_478_00,
                             statement_date=today - timedelta(days=20), due_date=due))  # fmt: skip
        pay = db.scalar(select(Category.id).where(Category.name == "Credit card payment"))
        for i, (days_ago, cents, words, category) in enumerate(
            [
                (5, 1_000_00, "CAPITAL ONE MOBILE PYMT", pay),
                (4, 30_00, "AMAZON REFUND", None),  # a refund isn't a payment
                (25, 2_000_00, "CAPITAL ONE MOBILE PYMT", pay),  # before the statement
            ]
        ):
            db.add(Transaction(account_id=card, external_id=f"p{i}", date=today - timedelta(days=days_ago),
                               amount_cents=cents, merchant=words, description=words, source="plaid",
                               category_id=category))  # fmt: skip

    [row] = [e for e in client.get("/api/upcoming").json()["events"] if e["kind"] == "card"]
    assert row["title"] == "Venture X ••8868 due" and row["cents"] == -3_478_00
    assert row["payment"]["paid_cents"] == 1_000_00
    [c] = client.get("/api/cards").json()["cards"]
    assert c["statement"]["payment"]["left_cents"] == 3_478_00

    with db_of(client).session() as db:
        assert cardpay.queue(db, today) == 1
        assert cardpay.queue(db, today) == 0  # once per statement and step
        e = db.scalar(select(AlertEvent))
        assert e.kind == "card" and e.link == f"/cards?card={card}" and "$" not in e.title + e.body
        assert alerts.priority_of(None, e.kind) == "high"
        # Held for quiet hours, then paid: it never goes out.
        e.status = "held"
    with db_of(client).session() as db:
        db.add(Transaction(account_id=card, external_id="p9", date=today, amount_cents=3_478_00,
                           merchant="PAYMENT THANK YOU", description="PAYMENT THANK YOU", source="plaid"))  # fmt: skip
    with db_of(client).session() as db:
        assert cardpay.queue(db, today + timedelta(days=2)) == 0
        assert db.scalar(select(AlertEvent)) is None
    [row] = [e for e in client.get("/api/upcoming").json()["events"] if e["kind"] == "card"]
    assert row["title"] == "Venture X ••8868 paid" and row["cents"] is None
