from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from financeapp import cards, offers
from financeapp.api import card_routes
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import AlertEvent, CardOffer
from tests.conftest import csrf, db_of, needs_db
from tests.test_agents import call, new_key
from tests.test_cards import TODAY, VALUES, card, credit_card, spend

# The text of the Chase Offers page as the bookmark copies it (document.body.innerText), with the bits around it.
CHASE_PAGE = """Skip to main content
Chase Offers
Sapphire Preferred (...9791)
$38.78 earned
New
All
Shopping
Groceries
Home
Fresh for Fall
Teleflora
10% cash back
Expiring soon
Pandora Jewelry
10% cash back
Tommy Hilfiger
15% cash back
Expiring soon
All offers
Food Lion
5% cash back
Marathon Fuel
5% cash back
In-store only
adidas
$20 cash back
Spend $100 or more
HarrisTeeter.com
5% cash back
Added
Starbucks
10% cash back
3 days left
Online only
Max $5 cash back
Old Navy
5% cash back
Expires 10/01/2026
"""

CAPITAL_ONE_PAGE = """Capital One Offers
Walmart
Up to 5% back
Expedia Earn 5X miles
Ends Nov 30
Best Buy · Up to $40 back
"""


def test_reading_a_chase_offers_page():
    found = offers.parse(CHASE_PAGE, TODAY)
    by = {r.merchant: r for r in found}
    assert list(by) == [
        "Teleflora", "Pandora Jewelry", "Tommy Hilfiger", "Food Lion", "Marathon Fuel", "adidas", "HarrisTeeter.com",
        "Starbucks",
    ]  # fmt: skip  # Old Navy ended on Oct 1
    assert by["Teleflora"].soon and not by["Pandora Jewelry"].soon
    assert (by["Food Lion"].kind, by["Food Lion"].value) == ("percent", 5)
    assert by["Marathon Fuel"].channel == "in store" and by["Starbucks"].channel == "online"
    assert (by["adidas"].kind, by["adidas"].value, by["adidas"].min_spend_cents) == ("amount", 20, 100_00)
    assert by["HarrisTeeter.com"].added is True and by["Food Lion"].added is None
    assert by["Starbucks"].expires == TODAY + timedelta(days=3) and by["Starbucks"].max_cents == 5_00
    assert by["Teleflora"].json("chase")["label"] == "10% back"
    assert offers.card_on_page(CHASE_PAGE) == ("Sapphire Preferred", "9791")
    assert offers.issuer_of("secure.chase.com", CHASE_PAGE) == "chase"
    assert offers.issuer_of("evil-chase.com", "") == "other"


def test_reading_a_capital_one_offers_page():
    found = {r.merchant: r for r in offers.parse(CAPITAL_ONE_PAGE, TODAY)}
    assert list(found) == ["Walmart", "Expedia", "Best Buy"]
    assert found["Walmart"].up_to and found["Walmart"].json("capital_one")["label"] == "Up to 5% back"
    assert found["Expedia"].kind == "multiplier" and found["Expedia"].expires == date(2026, 11, 30)
    assert found["Expedia"].json("capital_one")["label"] == "5X miles"
    assert offers.issuer_of("capitaloneoffers.com", "") == "capital_one"


def test_pages_with_no_offers_and_odd_text():
    assert offers.parse("Sign in\nUsername\nPassword", TODAY) == []
    assert offers.parse("Purchase APR 24.99%\nBalance $1,204.17", TODAY) == []
    assert offers.parse("5% cash back\n10% cash back", TODAY) == []  # no store names
    assert offers.parse("Big Sale\n80% cash back", TODAY) == []  # not a real offer
    assert offers.card_on_page("no card here") == (None, None)


def test_matching_offer_stores_to_purchases():
    assert offers.same_store("harristeeter com", "harris teeter")
    assert offers.same_store("marathon fuel", "marathon petro")
    assert offers.same_store("food lion", "food lion")
    assert offers.same_store("starbucks", "starbucks store")
    assert not offers.same_store("food lion", "lion brand yarn")
    assert not offers.same_store("the home depot", "the cheesecake factory")
    assert not offers.same_store("", "food lion")


def offer(account_id: int, merchant: str, kind: str = "percent", value: float = 5, **kw) -> offers.Offer:
    return offers.Offer(1, account_id, "chase", merchant, cards.merchant_key(merchant), kind, value, **kw)


def test_what_an_offer_is_worth():
    assert offers.worth(offer(1, "x"), 100_00, 1) == 5_00
    assert offers.worth(offer(1, "x", value=10, max_cents=5_00), 100_00, 1) == 5_00
    assert offers.worth(offer(1, "x", value=10, max_cents=5_00), 20_00, 1) == 2_00
    assert offers.worth(offer(1, "x", "amount", 20, min_spend_cents=100_00), 50_00, 1) == 0
    assert offers.worth(offer(1, "x", "amount", 20), 10_00, 1) == 10_00  # never more than the purchase
    assert offers.worth(offer(1, "x", "multiplier", 5), 100_00, 1.25) == 6_25


def test_an_offer_can_change_the_best_card():
    gold, csp = card(1, "amex-gold"), card(2, "chase-sapphire-preferred")
    plain = cards.lookup([gold, csp], [], TODAY, VALUES, cards.Ledger(), "Target")
    assert plain["channels"][0]["cards"][0]["account_id"] in (1, 2) and "offers" in plain and plain["offers"] == []
    here = [offer(2, "Target", value=10, channel="in store", expires=TODAY + timedelta(days=2))]
    found = cards.lookup([gold, csp], [], TODAY, VALUES, cards.Ledger(), "Target", offers=here)
    in_store, online = (c["cards"] for c in found["channels"])
    top = in_store[0]
    assert top["account_id"] == 2 and top["value_cents"] == 100 + 10_00  # 1x plus 10% back
    assert top["offer"]["name"] == "Chase Offer" and top["offer"]["ends"] == "Ends Thursday"
    assert any("Chase app" in w for w in top["warnings"])  # not added to the card yet
    assert "offer" not in next(r for r in online if r["account_id"] == 2)  # in store only
    assert found["offers"][0]["card"] == "Sapphire Preferred ••0002"
    other = cards.lookup([gold, csp], [], TODAY, VALUES, cards.Ledger(), "Costco", offers=here)
    assert other["offers"] == []


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(card_routes, "today", lambda: TODAY)


@needs_db
def test_sending_offers_from_the_page(client, home, fixed_today):
    home.setup()
    csp = credit_card(client, "Sapphire Preferred", "Chase", "9791", 860)
    venture = credit_card(client, "Venture", "Capital One", "4410", 120)
    spend(client, csp, "FOOD LION #1234", 80, 10, "5411")
    spend(client, venture, "Food Lion 0099", 60, 40, "5411")
    h = csrf(client)
    client.put(f"/api/cards/{csp}/link", json={"product": "chase-sapphire-preferred"}, headers=h)
    client.put(f"/api/cards/{venture}/link", json={"product": "capital-one-venture"}, headers=h)

    read = client.post("/api/cards/offers/read", json={"text": CHASE_PAGE, "site": "secure.chase.com"}, headers=h)
    r = read.json()
    assert r["issuer"] == "chase" and r["account_id"] == csp and r["mask"] == "9791" and len(r["offers"]) == 8
    with db_of(client).session() as db:
        assert db.scalar(select(CardOffer.id)) is None  # nothing saved yet

    page = client.put(f"/api/cards/{csp}/offers", json={"issuer": "chase", "offers": r["offers"]}, headers=h).json()
    o = page["offers"]
    assert o["cards"][0]["count"] == 8 and o["cards"][0]["seen"] == TODAY.isoformat()
    first = o["items"][0]
    assert first["merchant"] == "Food Lion" and first["visits"] == 2 and first["spent_cents"] == 140_00
    found = client.get("/api/cards/lookup", params={"q": "food lion"}).json()
    top = found["channels"][0]["cards"][0]
    assert top["account_id"] == csp and top["offer"]["label"] == "5% back"

    # A second send replaces the card's list; the other card's offers stay.
    client.put(f"/api/cards/{venture}/offers", json={"issuer": "capital_one", "offers": []}, headers=h)
    page = client.put(
        f"/api/cards/{csp}/offers", json={"issuer": "chase", "offers": r["offers"][3:5]}, headers=h
    ).json()
    assert [i["merchant"] for i in page["offers"]["items"]] == ["Food Lion", "Marathon Fuel"]
    page = client.delete(f"/api/cards/offers/{page['offers']['items'][1]['id']}", headers=h).json()
    assert [i["merchant"] for i in page["offers"]["items"]] == ["Food Lion"]

    bad = {"issuer": "chase", "offers": [{"merchant": "x", "kind": "percent", "value": -1}]}
    assert client.put(f"/api/cards/{csp}/offers", json=bad, headers=h).status_code == 422
    assert client.put("/api/cards/99999/offers", json={"issuer": "chase", "offers": []}, headers=h).status_code == 404

    key = new_key(client)
    answer = call(client, key, "best_card", {"store": "Food Lion", "amount": 100})["structuredContent"]
    assert answer["by_channel"][0]["cards"][0]["card_offer"] == "Chase Offer: 5% back"


@needs_db
def test_a_push_before_an_offer_you_would_use_ends(client, home):
    home.setup()
    csp = credit_card(client, "Sapphire Preferred", "Chase", "9791", 860)
    for days in (5, 30, 60):
        spend(client, csp, "STARBUCKS STORE 0042", 6, days, "5814")
    spend(client, csp, "Teleflora", 60, 90)
    found = offers.parse(CHASE_PAGE, TODAY)
    with db_of(client).session() as db:
        offers.replace(db, csp, "chase", found, TODAY)
    with db_of(client).session() as db:
        assert offers.queue(db, TODAY) == 1  # Starbucks ends in 3 days; Teleflora has no date and one visit
        assert offers.queue(db, TODAY) == 0  # once
        e = db.scalar(select(AlertEvent))
        assert e.title == "Chase Offer at Starbucks ends Friday"
        assert e.body == "10% back on Sapphire Preferred ••9791. Add it in the Chase app or on chase.com first."
        assert offers.queue(db, TODAY + timedelta(days=10)) == 0  # ended


def test_demo_mode_offers(tmp_path):
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    page = c.get("/api/cards").json()
    items = page["offers"]["items"]
    assert items and items[0]["visits"] > 0 and {i["card"] for i in items} == {"Sapphire Preferred ••9012"}
    found = c.get("/api/cards/lookup", params={"q": "target"}).json()
    assert found["channels"][0]["cards"][0].get("offer")
    assert c.post("/api/cards/offers/read", json={"text": CHASE_PAGE}).status_code == 403
    assert c.put("/api/cards/2/offers", json={"issuer": "chase", "offers": []}).status_code in (401, 403)
