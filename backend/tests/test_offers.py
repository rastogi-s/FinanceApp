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
    assert offers.issuer_of("notcapitalone.com", "") == "other"


def test_capital_one_stores_shown_only_as_logos():
    # Capital One's tiles show the store as a logo; the bookmark sends each picture's label as a line before it.
    page = """Capital One Offers
Venture X Card Art
Recommended
Walmart logo
Up to 5% back
Online
Logo for Expedia
5X miles
Ends Nov 30
Best Buy
Up to $40 back
"""
    found = offers.parse(page, TODAY)
    assert [r.merchant for r in found] == ["Walmart", "Expedia", "Best Buy"]
    assert found[0].channel == "online" and found[1].expires == date(2026, 11, 30)
    assert offers.parse("Venture X Card Art\nUp to 5% back", TODAY) == []


def test_capital_one_tag_lines_are_not_stores():
    # Each tile: the logo's label, a tag line over the amount, the amount. The tag line isn't the store.
    page = """Capital One Offers
Venture X ...8868
Walmart logo
In-Store & Online
Up to 13X miles
Logo for Sephora
Today's Top Offer. Limited availability.
25X miles
Starbucks
In-Store & In-App
5X miles
Shell
At the Pump
3X miles
Expedia
Up to 3,700 miles
6X miles
eBay
eBay Deals
Up to 50% back
Target
In-Store Only
5% back
Old Navy
Online Only
10% back
In-Store & Online
2X miles
"""
    found = {r.merchant: r for r in offers.parse(page, TODAY)}
    assert list(found) == ["Walmart", "Sephora", "Starbucks", "Shell", "Expedia", "eBay", "Target", "Old Navy"]
    assert found["Walmart"].channel is None and found["Shell"].channel == "in store"
    assert found["Target"].channel == "in store" and found["Old Navy"].channel == "online"
    assert found["Walmart"].up_to and found["Sephora"].value == 25


def test_capital_one_logos_name_the_store_by_website():
    # What the bookmark sends from capitaloneoffers.com: each logo's website, the tile's tagline, the amount.
    page = """Capital One Offers
Venture X ...8868
Featured Offers for You
Logo for www.sephora.com
Up to 13X miles
Logo for officedepot.com
Office
5X miles
Logo for ulta.com
12 Piece Holiday Beauty Box!
12X miles
Venture X Exclusive
Logo for stubhub.com
Up to 6X miles
Logo for walmart.com
Your seat at the World Series awaits. ...
6X miles
Members get free shipping always.
4X miles
Introducing ScoreCard+
3X miles
"""
    found = offers.parse(page, TODAY)
    assert [r.merchant for r in found] == ["sephora.com", "officedepot.com", "ulta.com", "stubhub.com", "walmart.com"]
    assert offers.same_store(cards.merchant_key("officedepot.com"), cards.merchant_key("OFFICE DEPOT #1234"))


AMEX_PAGE = """Amex Offers
Gold Card ...41007
Available
Dell Technologies
Spend $250 or more, earn $50 back
Expires 12/31/2026
Add to Card
Hilton
Earn +3 Membership Rewards points per eligible dollar spent
Expires 11/30/2026
Added to Card
Shake Shack
Earn 20% back on purchases, up to a total of $15
Expires 12/15/2026
"""


def test_reading_other_banks_offers_pages():
    found = {r.merchant: r for r in offers.parse(AMEX_PAGE, TODAY)}
    assert list(found) == ["Dell Technologies", "Hilton", "Shake Shack"]
    dell, hilton, shack = found.values()
    assert (dell.kind, dell.value, dell.min_spend_cents, dell.added) == ("amount", 50, 250_00, False)
    assert (hilton.kind, hilton.value, hilton.added) == ("multiplier", 3, True)
    assert (shack.kind, shack.value, shack.max_cents) == ("percent", 20, 15_00)
    assert offers.issuer_of("global.americanexpress.com", AMEX_PAGE) == "amex"
    assert offers.card_on_page(AMEX_PAGE) == ("Gold Card", "1007")
    assert offers.card_on_page("Platinum Card® -71003") == ("Platinum Card®", "1003")
    assert hilton.json("amex")["label"] == "3X points"

    citi = offers.parse("Citi Merchant Offers\nWalgreens\n10% cash back\nOnline only\nEnroll", TODAY)
    assert [(r.merchant, r.value, r.channel, r.added) for r in citi] == [("Walgreens", 10, "online", False)]
    bofa = offers.parse("BankAmeriDeals\nStarbucks\n10% Cash Back\n12 days left\nTarget\n5% Cash Back", TODAY)
    assert [r.merchant for r in bofa] == ["Starbucks", "Target"] and bofa[0].expires == TODAY + timedelta(days=12)
    wells = offers.parse("My Wells Fargo Deals\nPanera Bread · 15% cash back\nExpires Nov 30", TODAY)
    assert [(r.merchant, r.value) for r in wells] == [("Panera Bread", 15)]
    us = offers.parse("Lowe's\nGet $10 back\nwhen you spend $100 or more", TODAY)
    assert [(r.merchant, r.value, r.min_spend_cents) for r in us] == [("Lowe's", 10, 100_00)]
    disco = offers.parse("Discover Deals\nWalmart\n5% cashback\nTarget\n3 points per $1", TODAY)
    assert [(r.merchant, r.kind) for r in disco] == [("Walmart", "percent"), ("Target", "multiplier")]
    for site, bank in [("online.citi.com", "citi"), ("secure.bankofamerica.com", "bofa"),
                       ("connect.secure.wellsfargo.com", "wells_fargo"), ("onlinebanking.usbank.com", "us_bank"),
                       ("card.discover.com", "discover"), ("evil-amex.com", "other")]:  # fmt: skip
        assert offers.issuer_of(site, "") == bank


def test_pages_with_no_offers_and_odd_text():
    assert offers.parse("Sign in\nUsername\nPassword", TODAY) == []
    assert offers.parse("Purchase APR 24.99%\nBalance $1,204.17", TODAY) == []
    assert offers.parse("5% cash back\n10% cash back", TODAY) == []  # no store names
    assert offers.parse("Big Sale\n80% cash back", TODAY) == []  # not a real offer
    assert offers.card_on_page("no card here") == (None, None)
    assert offers.card_on_page("Venture X  ending in 4410") == ("Venture X", "4410")
    assert offers.parse("Food Lion\n" + " " * 50_000 + "x\nup to" + " " * 50_000, TODAY) == []  # quick


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
        assert e.title == "Chase Offer at Starbucks ends Friday" and e.link == "/cards"
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
