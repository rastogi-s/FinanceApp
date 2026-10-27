"""What banks pay on average, and what Treasuries pay, to compare an account's rate with.

- National averages: the FDIC's National Rates page (savings, interest checking, money market and CDs from 1 to 60
  months), posted on the third Monday of each month.
- Treasury yields: FRED's daily constant-maturity series, 1 month to 5 years (DGS1MO ... DGS5), no key needed.

Checked once a day by the scheduler and kept in the "market_rates" preference; until the first check works (or with
quotes turned off) the numbers bundled in data/rates.json are used. Only public pages are fetched; nothing about the
household is sent.
"""

import html
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import Preference, now

log = logging.getLogger("financeapp")
PREF = "market_rates"
EVERY = timedelta(hours=20)
FDIC_URL = "https://www.fdic.gov/national-rates-and-rate-caps"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
TREASURY = {"1": "DGS1MO", "3": "DGS3MO", "6": "DGS6MO", "12": "DGS1", "24": "DGS2", "36": "DGS3", "60": "DGS5"}
FDIC_ROW = re.compile(r"(Savings|Interest Checking|Money Market|(\d+) month CD)\s+(\d+\.\d+)")
FDIC_DATE = re.compile(r"as of ([A-Z][a-z]+ \d{1,2}, \d{4})")
BUNDLED = json.loads((Path(__file__).parent / "data" / "rates.json").read_text())


def current(db) -> dict:
    p = db.get(Preference, PREF)
    saved = (p.value if p else {}) or {}
    return {"fdic": saved.get("fdic") or BUNDLED["fdic"], "treasury": saved.get("treasury") or BUNDLED["treasury"]}


def nearest_term(months: int, terms: list[str]) -> str:
    return min(terms, key=lambda t: (abs(int(t) - months), int(t)))


def benchmark(rates: dict, kind: str, term_months: int | None = None) -> dict:
    """The national average for this kind of account and the Treasury yield over about the same time. Savings and
    checking compare with a 3-month bill: money you can take out any time."""
    fdic, treasury = rates["fdic"], rates["treasury"]
    if kind == "cd":
        months = term_months or 12
        national = fdic["cd"][nearest_term(months, list(fdic["cd"]))]
        label = f"{nearest_term(months, list(fdic['cd']))}-month CD"
    else:
        months = 3
        key = {"checking": "checking", "money market": "money_market"}.get(kind, "savings")
        national = fdic[key]
        label = {"checking": "interest checking", "money_market": "money market"}.get(key, "savings")
    t = nearest_term(months, [k for k in treasury if k.isdigit()])
    return {
        "national": national,
        "national_label": label,
        "national_as_of": fdic["as_of"],
        "treasury": treasury[t],
        "treasury_label": f"{t}-month Treasury" if int(t) < 12 else f"{int(t) // 12}-year Treasury",
        "treasury_as_of": treasury["as_of"],
    }


def parse_fdic(page: str) -> dict | None:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    start = text.find("National Deposit Rates")
    if start < 0:
        return None
    out: dict = {"source": FDIC_URL, "cd": {}}
    when = FDIC_DATE.search(text)
    if when:
        out["as_of"] = datetime.strptime(when.group(1), "%B %d, %Y").date().isoformat()
    for m in FDIC_ROW.finditer(text, start):
        rate = float(m.group(3))
        if m.group(2):
            out["cd"].setdefault(str(int(m.group(2))), rate)
        else:
            out.setdefault(
                {"Savings": "savings", "Interest Checking": "checking"}.get(m.group(1), "money_market"), rate
            )
    complete = all(k in out for k in ("as_of", "savings", "checking", "money_market")) and "12" in out["cd"]
    return out if complete else None


def parse_fred(csv: str) -> tuple[date, float] | None:
    """The newest value in a FRED download (holidays have an empty value)."""
    for line in reversed(csv.strip().splitlines()[1:]):
        day, _, value = line.partition(",")
        try:
            return date.fromisoformat(day), float(value)
        except ValueError:
            continue
    return None


def refresh(database: Database, settings: Settings, client: httpx.Client | None = None) -> bool | None:
    """Fetches both, once a day. None when it wasn't due; False when nothing new could be read."""
    if settings.quotes_provider.lower() == "off":
        return None
    with database.session() as db:
        p = db.get(Preference, PREF)
        saved = dict(p.value) if p else {}
    checked = saved.get("checked")
    if checked and datetime.fromisoformat(checked) > now() - EVERY:
        return None
    own = client is None
    client = client or httpx.Client(
        timeout=15, headers={"User-Agent": "FinanceApp (self-hosted)"}, follow_redirects=True
    )
    fdic = treasury = None
    try:
        try:
            r = client.get(FDIC_URL)
            r.raise_for_status()
            fdic = parse_fdic(r.text)
        except httpx.HTTPError as e:
            log.info("national rates not read: %s", e)
        since = (date.today() - timedelta(days=21)).isoformat()
        found: dict = {"source": "https://fred.stlouisfed.org"}
        for months, series in TREASURY.items():
            try:
                r = client.get(FRED_URL, params={"id": series, "cosd": since})
                r.raise_for_status()
                newest = parse_fred(r.text)
            except httpx.HTTPError:
                newest = None
            if newest:
                found[months] = newest[1]
                found["as_of"] = max(found.get("as_of", ""), newest[0].isoformat())
        treasury = found if all(k in found for k in TREASURY) else None
    finally:
        if own:
            client.close()
    with database.session() as db:
        p = db.get(Preference, PREF) or Preference(key=PREF, value={})
        value = dict(p.value) | {"checked": now().isoformat()}
        if fdic:
            value["fdic"] = fdic
        if treasury:
            value["treasury"] = treasury
        p.value = value
        db.merge(p)
    return bool(fdic or treasury)
