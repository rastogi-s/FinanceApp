"""Live prices for holdings: every 15 minutes while US markets are open, then twice after the close (at 4:05 PM
Eastern for the closing price, and at 6:30 PM for mutual funds, which price once a day after the close).

Only ticker symbols ever leave the server: no quantities, values, accounts or anything about who is asking. Two
sources (QUOTES_PROVIDER): Finnhub's official free tier (needs FINNHUB_API_KEY; stocks and ETFs) and Yahoo
Finance's public chart data (no key; has mutual funds too). "auto" uses Finnhub when there's a key and Yahoo for
the rest. A failed lookup just leaves the last price in place: holdings fall back to the price from the last sync.

Trends. Once a day after the close, a year of daily closes from Yahoo gives each symbol's 52-week high and low and its
50- and 200-day averages (Finnhub's free tier has no history), for the dip alerts in alerts.py.

Market indexes. The S&P 500, Nasdaq and Dow (INDEXES) are looked up from Yahoo too, when a market drop alert watches
one. For them the trend also keeps the record close: once, from all of Yahoo's monthly history, then from each year of
daily closes, so the drop is measured from the all-time high, not the 52-week one (a bear market can last longer than a
year).
"""

import logging
import re
import time as clock
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as quote_path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import Account, AlertRule, HoldingSnapshot, Preference, Quote, now

log = logging.getLogger("financeapp")
ET = ZoneInfo("America/New_York")
# Plain exchange tickers only (VOO, BRK.B, BF-B). Anything else (a fund name, an option, a CUSIP) isn't looked up.
TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-][A-Z])?$")
QUOTED_CLASSES = {"Stocks", "Funds", "Bonds"}
# Market indexes a drop alert can watch: Yahoo's symbol and the name people know.
INDEXES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow"}
AFTER_CLOSE = (time(16, 5), time(18, 30))
MAX_SYMBOLS = 150
FINNHUB_PER_MINUTE = 55  # the free tier allows 60 calls a minute


@dataclass
class QuoteData:
    symbol: str
    price: Decimal
    previous_close: Decimal | None
    as_of: datetime
    source: str


def quotable(symbol: str, asset_class: str = "Stocks") -> bool:
    return asset_class in QUOTED_CLASSES and bool(TICKER.match(symbol))


def is_index(symbol: str) -> bool:
    return symbol in INDEXES


def yahoo_path(symbol: str) -> str:
    """Yahoo writes share classes with a dash (BRK-B); an index keeps its caret, escaped."""
    return quote_path(symbol.replace(".", "-"), safe="-")


def market_open(t: datetime) -> bool:
    """Regular US trading hours (holidays aren't known; on one, the price just doesn't change)."""
    et = t.astimezone(ET)
    return et.weekday() < 5 and time(9, 30) <= et.time() < time(16, 0)


def due(last: datetime | None, t: datetime, interval_minutes: int) -> bool:
    if last is None:
        return True
    et = t.astimezone(ET)
    marks = [datetime.combine(et.date(), m, ET) for m in AFTER_CLOSE] if et.weekday() < 5 else []
    if any(last < m <= t for m in marks):
        return True  # the closing price, and later the mutual funds'
    return market_open(t) and t - last >= timedelta(minutes=interval_minutes)


def _decimal(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() and d > 0 else None


def finnhub(client: httpx.Client, key: str, symbols: list[str]) -> dict[str, QuoteData]:
    out = {}
    for i, s in enumerate(symbols):
        if i and i % FINNHUB_PER_MINUTE == 0:
            clock.sleep(61)
        r = client.get("https://finnhub.io/api/v1/quote", params={"symbol": s}, headers={"X-Finnhub-Token": key})
        if r.status_code == 429:
            log.info("finnhub rate limit reached; the rest wait for the next round")
            break
        r.raise_for_status()
        data = r.json()
        price = _decimal(data.get("c"))
        if price is None or not data.get("t"):  # Finnhub answers 0 for symbols it doesn't have
            continue
        out[s] = QuoteData(s, price, _decimal(data.get("pc")), datetime.fromtimestamp(int(data["t"]), UTC), "finnhub")
    return out


def yahoo(client: httpx.Client, symbols: list[str]) -> dict[str, QuoteData]:
    out = {}
    for s in symbols:
        # TICKER (or INDEXES) has already made the symbol safe for a URL path.
        r = client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_path(s)}",
            params={"range": "1d", "interval": "1d"},
        )
        if r.status_code == 404:
            continue
        r.raise_for_status()
        result = (r.json().get("chart") or {}).get("result") or []
        meta = result[0].get("meta", {}) if result else {}
        price = _decimal(meta.get("regularMarketPrice"))
        at = meta.get("regularMarketTime")
        if price is None or not isinstance(at, int) or (meta.get("currency") or "USD") != "USD":
            continue
        previous = _decimal(meta.get("chartPreviousClose") or meta.get("previousClose"))
        out[s] = QuoteData(s, price, previous, datetime.fromtimestamp(at, UTC), "yahoo")
    return out


def fetch(settings: Settings, symbols: list[str], client: httpx.Client | None = None) -> dict[str, QuoteData]:
    provider = settings.quotes_provider.lower()
    if provider == "off" or not symbols:
        return {}
    own = client is None
    client = client or httpx.Client(timeout=10, headers={"User-Agent": "FinanceApp (self-hosted)"})
    out: dict[str, QuoteData] = {}
    try:
        stocks = [s for s in symbols if not is_index(s)]  # Finnhub's free tier has no indexes
        if provider in ("auto", "finnhub") and settings.finnhub_api_key and stocks:
            try:
                out.update(finnhub(client, settings.finnhub_api_key, stocks))
            except httpx.HTTPError as e:
                log.warning("finnhub quotes failed (%s)", type(e).__name__)
        if provider == "yahoo" or (provider == "auto" and len(out) < len(symbols)):
            try:
                out.update(yahoo(client, [s for s in symbols if s not in out]))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("yahoo quotes failed (%s)", type(e).__name__)
    finally:
        if own:
            client.close()
    return out


# ---- what to look up, and saving it


def holding_symbols(db: DbSession) -> set[str]:
    """Every quotable symbol in the latest holdings of a counted account."""
    latest = (
        select(HoldingSnapshot.account_id, func.max(HoldingSnapshot.date).label("d"))
        .group_by(HoldingSnapshot.account_id)
        .subquery()
    )
    rows = db.execute(
        select(HoldingSnapshot.symbol, HoldingSnapshot.asset_class)
        .join(latest, (HoldingSnapshot.account_id == latest.c.account_id) & (HoldingSnapshot.date == latest.c.d))
        .join(Account, Account.id == HoldingSnapshot.account_id)
        .where(Account.hidden.is_(False), Account.is_authoritative.is_(True))
    ).all()
    return {s for s, c in rows if quotable(s, c)}


def market_symbols(db: DbSession) -> list[str]:
    """The indexes market drop alerts watch."""
    rules = db.scalars(select(AlertRule).where(AlertRule.kind == "market", AlertRule.enabled.is_(True)))
    return sorted({str(r.params.get("symbol")) for r in rules} & INDEXES.keys())


def held_symbols(db: DbSession) -> list[str]:
    """What to look up: the indexes market alerts watch, then every symbol held and those price and dip alerts watch."""
    symbols = holding_symbols(db)
    for r in db.scalars(select(AlertRule).where(AlertRule.kind.in_(["price", "dip"]), AlertRule.enabled.is_(True))):
        s = str(r.params.get("symbol") or "")
        if quotable(s):
            symbols.add(s)
    return (market_symbols(db) + sorted(symbols))[:MAX_SYMBOLS]


def save(db: DbSession, quotes: dict[str, QuoteData]) -> None:
    for q in quotes.values():
        values = {
            "symbol": q.symbol,
            "price": q.price,
            "previous_close": q.previous_close,
            "as_of": q.as_of,
            "source": q.source,
            "fetched_at": now(),
        }
        stmt = insert(Quote).values(**values)
        db.execute(stmt.on_conflict_do_update(index_elements=["symbol"], set_=values))


def last_run(db: DbSession) -> datetime | None:
    p = db.get(Preference, "quotes")
    return datetime.fromisoformat(p.value["last_run"]) if p and p.value.get("last_run") else None


def refresh(database: Database, settings: Settings, force: bool = False) -> int | None:
    """Looks up prices if it's time to (or `force`). Returns how many came back, or None if it wasn't time."""
    if settings.quotes_provider.lower() == "off":
        return None
    t = now()
    with database.session() as db:
        if not force and not due(last_run(db), t, settings.quote_interval_minutes):
            return None
        symbols = held_symbols(db)
        p = db.get(Preference, "quotes") or Preference(key="quotes", value={})
        p.value = {"last_run": t.isoformat()}
        db.merge(p)
    if not symbols:
        return 0
    quotes = fetch(settings, symbols)
    with database.session() as db:
        save(db, quotes)
    return len(quotes)


# ---- trends: a year of daily closes, once a day


def last_close_day(t: datetime) -> date:
    """The trading day whose close is the latest one in (weekends skipped; holidays aren't known)."""
    et = t.astimezone(ET)
    d = et.date() if et.time() >= AFTER_CLOSE[0] else et.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


SPARK_DAYS = 22  # about a month of trading days


def _average(closes: list[Decimal], n: int) -> Decimal | None:
    return (sum(closes[-n:]) / n).quantize(Decimal("0.0001")) if len(closes) >= n else None


def yahoo_closes(client: httpx.Client, symbol: str, through: date, range_: str, interval: str):
    """Daily (or monthly) closes up to `through`, and Yahoo's meta; None when Yahoo doesn't know the symbol."""
    r = client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_path(symbol)}",
        params={"range": range_, "interval": interval},
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta") or {}
    if (meta.get("currency") or "USD") != "USD":
        return None
    raw = ((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    days: list[tuple[date, Decimal]] = []
    for ts, c in zip(result[0].get("timestamp") or [], raw, strict=False):
        price = _decimal(c)
        if isinstance(ts, int) and price is not None:
            day = datetime.fromtimestamp(ts, UTC).astimezone(ET).date()
            if day <= through:  # not today's bar while the market is still open
                days.append((day, price))
    return days, meta


def record_close(days: list[tuple[date, Decimal]]) -> tuple[date, Decimal] | None:
    """The highest close, and the first day it closed there."""
    return max(days, key=lambda d: (d[1], -d[0].toordinal())) if days else None


def yahoo_trend(client: httpx.Client, symbol: str, through: date) -> dict | None:
    got = yahoo_closes(client, symbol, through, "1y", "1d")
    if got is None or not got[0]:
        return None
    days, meta = got
    closes = [c for _, c in days]
    high, low = _decimal(meta.get("fiftyTwoWeekHigh")), _decimal(meta.get("fiftyTwoWeekLow"))
    return {
        "year_high": max(high or closes[0], max(closes)),
        "year_low": min(low or closes[0], min(closes)),
        "average_50": _average(closes, 50),
        "average_200": _average(closes, 200),
        "trend_as_of": days[-1][0],
        "closes": [float(c) for c in closes[-SPARK_DAYS:]],
        "year_record": record_close(days),
    }


def peak_update(q: Quote, record: tuple[date, Decimal] | None, full: bool = False) -> None:
    """Keeps the highest close seen; `full` once it's been checked against all of the history."""
    if record is not None and (q.peak is None or record[1] > q.peak):
        q.peak_on, q.peak = record
    if full:
        q.peak_full = True


def refresh_trends(database: Database, t: datetime, client: httpx.Client | None = None) -> int:
    """Updates the trends of held symbols that don't have the latest close counted yet. Returns how many changed."""
    through = last_close_day(t)
    with database.session() as db:
        stale = [
            q.symbol for q in latest(db, held_symbols(db)).values() if q.trend_as_of is None or q.trend_as_of < through
        ]
    if not stale:
        return 0
    own = client is None
    client = client or httpx.Client(timeout=10, headers={"User-Agent": "FinanceApp (self-hosted)"})
    found: dict[str, dict] = {}
    history: dict[str, tuple[date, Decimal] | None] = {}  # an index's record close over all its history
    with database.session() as db:
        unchecked = [s for s in stale if is_index(s) and not getattr(db.get(Quote, s), "peak_full", True)]
    try:
        for s in unchecked:
            try:
                got = yahoo_closes(client, s, through, "max", "1mo")
            except (httpx.HTTPError, ValueError):
                continue  # tried again tomorrow; the year's record close counts meanwhile
            history[s] = record_close(got[0]) if got else None
        for s in stale:
            try:
                trend = yahoo_trend(client, s, through)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    log.info("yahoo rate limit reached; the rest of the trends wait for the next round")
                    break
                continue
            except (httpx.HTTPError, ValueError):
                continue
            found[s] = trend or {"trend_as_of": through}  # Yahoo has no history for it: don't ask again today
    finally:
        if own:
            client.close()
    with database.session() as db:
        for s, trend in found.items():
            q = db.get(Quote, s)
            if q is not None:
                record = trend.pop("year_record", None)
                for k, v in trend.items():
                    setattr(q, k, v)
                if is_index(s):
                    peak_update(q, record)
                    if s in history:
                        peak_update(q, history[s], full=True)
    return len(found)


def latest(db: DbSession, symbols: list[str] | set[str]) -> dict[str, Quote]:
    if not symbols:
        return {}
    return {q.symbol: q for q in db.scalars(select(Quote).where(Quote.symbol.in_(list(symbols))))}


def fresh_for(q: Quote | None, snapshot_day: date) -> bool:
    """A quote counts only when it is from after the start of the day the holdings were synced. The snapshot's day is
    the server's own date (sync.today), so the quote's time is read in the same zone: in Eastern time, a sync after
    8 PM ET on a UTC server is already "tomorrow" and no quote would count until the next morning."""
    return q is not None and q.as_of.astimezone().date() >= snapshot_day
