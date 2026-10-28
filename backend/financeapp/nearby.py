"""Offers near me: which of your card offers (offers.py) are at a store around you right now, in store only.

Your location is used once, when you ask (the Credit Cards page) or when an iPhone Shortcuts automation sends it as
you arrive somewhere. It is rounded to about 100 m, sent to OpenStreetMap's Overpass API to list the named shops,
restaurants and gas stations around it, and then forgotten: it isn't saved or logged. Store names (and the brand
OpenStreetMap knows a store by: "Food Lion" for "Food Lion #1234") are matched to your offers the same way purchases
are. Online-only offers are left out, since you can't use them at the store.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import offers
from financeapp.categorize import merchant_key
from financeapp.models import AlertEvent, now

log = logging.getLogger("financeapp")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RADIUS_M = 400  # about a 5 minute walk; a store across a big parking lot still counts
ROUND = 3  # decimal places kept: about 110 m, so the exact spot never leaves the app
MAX_PLACES = 300
AMENITIES = "fuel|restaurant|cafe|fast_food|pharmacy|bar|pub|ice_cream|cinema|car_rental|charging_station"


UNAVAILABLE = "Couldn't look up the stores around you. Try again in a minute."


class Unavailable(RuntimeError):
    """The map lookup didn't work. Callers show UNAVAILABLE, never the exception's text."""


@dataclass
class Place:
    name: str
    keys: tuple[str, ...]  # merchant_key of its name and brand
    meters: int


def rounded(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, ROUND), round(lon, ROUND)


def meters(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)))


def query(lat: float, lon: float, radius: int = RADIUS_M) -> str:
    around = f"around:{radius},{lat:.{ROUND}f},{lon:.{ROUND}f}"
    return (
        "[out:json][timeout:10];("
        f'nwr({around})["name"]["shop"];'
        f'nwr({around})["name"]["amenity"~"^({AMENITIES})$"];'
        f'nwr({around})["brand"]["shop"];'
        f");out center {MAX_PLACES};"
    )


def places(lat: float, lon: float, client: httpx.Client | None = None) -> list[Place]:
    """Named stores within RADIUS_M of a spot, nearest first. Raises Unavailable."""
    lat, lon = rounded(lat, lon)
    own = client is None
    client = client or httpx.Client(timeout=15, headers={"User-Agent": "FinanceApp (self-hosted)"})
    try:
        # The rounded spot can be up to ~80 m off, so look a little wider.
        r = client.post(OVERPASS_URL, data={"data": query(lat, lon, RADIUS_M + 100)})
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except (httpx.HTTPError, ValueError) as e:
        log.info("stores nearby not read: %s", type(e).__name__)
        raise Unavailable(UNAVAILABLE) from None
    finally:
        if own:
            client.close()
    out: dict[str, Place] = {}
    for el in elements[:MAX_PLACES]:
        tags = el.get("tags") or {}
        where = el.get("center") or el
        if "lat" not in where or "lon" not in where:
            continue
        name = str(tags.get("name") or tags.get("brand") or "")[:80]
        keys = tuple(dict.fromkeys(k for k in (merchant_key(name), merchant_key(str(tags.get("brand") or ""))) if k))
        if not keys:
            continue
        m = meters(lat, lon, float(where["lat"]), float(where["lon"]))
        if keys[0] not in out or m < out[keys[0]].meters:
            out[keys[0]] = Place(name, keys, m)
    return sorted(out.values(), key=lambda p: p.meters)


def matches(saved: list[offers.Offer], around: list[Place]) -> list[tuple[offers.Offer, Place]]:
    """Each in-store offer at a store nearby, with the nearest such store, nearest first."""
    found = []
    for o in saved:
        if o.channel == "online":
            continue
        place = next((p for p in around if any(offers.same_store(o.key, k) for k in p.keys)), None)
        if place is not None:
            found.append((o, place))
    return sorted(found, key=lambda f: (f[1].meters, -f[0].value))


def describe(o: offers.Offer, p: Place, card: str, on: date) -> dict:
    return {
        "id": o.id,
        "merchant": o.merchant,
        "store": p.name,
        "meters": p.meters,
        "label": o.label,
        "name": offers.OFFER_NAMES.get(o.issuer, "Card offer"),
        "card": card,
        "ends": offers.ends_note(o, on),
        "added": o.added,
        "how": None if o.added else offers.WHERE.get(o.issuer, offers.WHERE["other"]),
        "check_terms": o.channel is None,  # the bank's list didn't say it works in store
    }


def queue(db: DbSession, found: list[dict], on: date) -> int:
    """A push for each offer here, at most once a day per offer (arriving twice doesn't nag)."""
    added = 0
    for f in found:
        title = f"{f['name']} here: {f['merchant']}"
        body = f"{f['label']} on {f['card']}."
        if f["how"]:
            body += " " + f["how"]
        stmt = insert(AlertEvent).values(
            rule_id=None, kind="account", title=title[:120], body=body[:300],
            dedup_key=f"offer-here:{f['id']}:{on}"[:160], fired_at=now(), status="pending", attempts=0,
            link="/cards",
        )  # fmt: skip
        if db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None:
            added += 1
    return added
