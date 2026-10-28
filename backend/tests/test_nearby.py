from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from financeapp import cards, nearby, offers
from financeapp.api import card_routes, mcp_routes
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import AlertEvent
from tests.conftest import csrf, db_of, needs_db
from tests.test_agents import new_key
from tests.test_cards import TODAY, credit_card
from tests.test_offers import CHASE_PAGE

SPOT = (35.787654, -78.645678)
OSM = {
    "elements": [
        {"type": "node", "lat": 35.7880, "lon": -78.6460, "tags": {"name": "Food Lion", "shop": "supermarket"}},
        {"type": "way", "center": {"lat": 35.7890, "lon": -78.6440}, "tags": {"name": "Marathon", "amenity": "fuel"}},
        {"type": "node", "lat": 35.7875, "lon": -78.6455, "tags": {"brand": "Starbucks", "shop": "coffee"}},
        {"type": "node", "lat": 35.7876, "lon": -78.6458, "tags": {"name": "Teleflora", "shop": "florist"}},
        {"type": "node", "tags": {"name": "No place"}},
    ]
}


def overpass(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json=OSM)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_stores_around_a_rounded_spot():
    seen: list = []
    around = nearby.places(*SPOT, client=overpass(seen))
    assert "35.788" in httpx.QueryParams(seen[0])["data"] and "35.787654" not in seen[0]  # rounded before it leaves
    assert around[0].name == "Food Lion" and len(around) == 4  # distances from the rounded spot
    assert all(p.meters < 500 for p in around)


def test_a_failed_lookup_says_so():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504)

    with pytest.raises(nearby.Unavailable):
        nearby.places(*SPOT, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_only_in_store_offers_at_stores_nearby():
    saved = [
        offers.Offer(i, 1, "chase", r.merchant, cards.merchant_key(r.merchant), r.kind, r.value, channel=r.channel)
        for i, r in enumerate(offers.parse(CHASE_PAGE, TODAY))
    ]
    around = nearby.places(*SPOT, client=overpass([]))
    found = nearby.matches(saved, around)
    # Starbucks's offer is online only; Teleflora's doesn't say, so it counts (with "check the terms").
    assert {o.merchant for o, _ in found} == {"Food Lion", "Marathon Fuel", "Teleflora"}


@needs_db
def test_offers_near_me_and_the_shortcuts_hook(client, home, monkeypatch):
    home.setup()
    csp = credit_card(client, "Sapphire Preferred", "Chase", "9791", 860)
    h = csrf(client)
    client.put(f"/api/cards/{csp}/link", json={"product": "chase-sapphire-preferred"}, headers=h)
    monkeypatch.setattr(card_routes, "today", lambda: TODAY)
    monkeypatch.setattr(mcp_routes, "today", lambda: TODAY)
    r = client.post("/api/cards/offers/near", json={"lat": SPOT[0], "lon": SPOT[1]}, headers=h).json()
    assert r["offers"] == []  # no offers saved: no lookup

    read = client.post("/api/cards/offers/read", json={"text": CHASE_PAGE, "site": "secure.chase.com"}, headers=h)
    client.put(f"/api/cards/{csp}/offers", json={"issuer": "chase", "offers": read.json()["offers"]}, headers=h)
    real = nearby.places
    monkeypatch.setattr(nearby, "places", lambda lat, lon: real(lat, lon, client=overpass([])))
    r = client.post("/api/cards/offers/near", json={"lat": SPOT[0], "lon": SPOT[1]}, headers=h).json()
    by = {o["merchant"]: o for o in r["offers"]}
    assert set(by) == {"Food Lion", "Marathon Fuel", "Teleflora"} and r["stores"] == 4
    assert by["Marathon Fuel"]["store"] == "Marathon" and not by["Marathon Fuel"]["check_terms"]
    assert by["Food Lion"]["check_terms"] and by["Food Lion"]["card"] == "Sapphire Preferred ••9791"
    assert client.post("/api/cards/offers/near", json={"lat": 91, "lon": 0}, headers=h).status_code == 422

    key = new_key(client)
    hook = "/hook/offers-here"
    assert client.post(hook, json={"lat": SPOT[0], "lon": SPOT[1]}).status_code == 401
    bearer = {"Authorization": f"Bearer {key}"}
    assert client.post(hook, json={"lat": "x"}, headers=bearer).status_code == 422
    assert client.post(hook, json=SPOT_JSON, headers={**bearer, "Origin": "https://evil.example"}).status_code == 403
    assert client.post(hook, json=SPOT_JSON, headers=bearer).json() == {"found": 3, "pushed": 3}
    assert client.post(hook, json=SPOT_JSON, headers=bearer).json() == {"found": 3, "pushed": 0}  # once a day
    with db_of(client).session() as db:
        titles = sorted(e.title for e in db.scalars(select(AlertEvent)))
    assert titles == ["Chase Offer here: Food Lion", "Chase Offer here: Marathon Fuel", "Chase Offer here: Teleflora"]
    monkeypatch.setattr(mcp_routes, "today", lambda: TODAY + timedelta(days=1))
    assert client.post(hook, json=SPOT_JSON, headers=bearer).json()["pushed"] == 3


SPOT_JSON = {"lat": SPOT[0], "lon": SPOT[1]}


def test_demo_mode_offers_near(tmp_path):
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    assert c.post("/api/cards/offers/near", json=SPOT_JSON).status_code == 403
    assert c.post("/hook/offers-here", json=SPOT_JSON).status_code == 404
