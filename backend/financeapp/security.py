"""The detail page of one holding: price chart, the day's and year's ranges, key numbers, and your position in it.

Market data. Prices and charts come from Yahoo Finance's public chart data (no key; stocks, ETFs and mutual funds);
dividends over the last year give the yield. With FINNHUB_API_KEY set, Finnhub's official free tier adds company
numbers: market value, P/E, beta, industry. A fund's own holdings come from its SEC filing (lookthrough.py).

Everything is fetched by the server and kept for a while (charts for minutes, company numbers for a day), so the
browser only ever talks to this app. Only the ticker symbol is sent anywhere.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx

from financeapp.config import Settings
from financeapp.quotes import TICKER

log = logging.getLogger("financeapp")
# The chart ranges the page offers: Yahoo's range, its bar size, and how long a copy is kept (seconds).
RANGES = {
    "1d": ("1d", "5m", 300),
    "5d": ("5d", "30m", 900),
    "1m": ("1mo", "1d", 3600),
    "6m": ("6mo", "1d", 3600),
    "1y": ("1y", "1d", 3600 * 6),
    "5y": ("5y", "1wk", 3600 * 12),
}
PROFILE_SECONDS = 24 * 3600
MAX_CACHE = 400

_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: tuple, seconds: int, load):
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > monotonic():
            return hit[1]
    value = load()
    with _lock:
        if len(_cache) >= MAX_CACHE:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[: MAX_CACHE // 4]:
                _cache.pop(k, None)
        _cache[key] = (monotonic() + seconds, value)
    return value


def valid(symbol: str) -> bool:
    return bool(TICKER.match(symbol))


def _client() -> httpx.Client:
    return httpx.Client(timeout=10, headers={"User-Agent": "FinanceApp (self-hosted)"})


def _num(v) -> float | None:
    return float(v) if isinstance(v, int | float) and v == v else None  # v == v drops NaN


def chart(symbol: str, range_: str) -> dict | None:
    """Price points for the range, and the numbers Yahoo sends with them. None if Yahoo doesn't know the symbol."""
    yahoo_range, interval, seconds = RANGES[range_]

    def load() -> dict | None:
        with _client() as c:
            r = c.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.replace('.', '-')}",
                params={"range": yahoo_range, "interval": interval, "events": "div"},
            )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        result = (r.json().get("chart") or {}).get("result") or []
        if not result:
            return None
        res = result[0]
        meta = res.get("meta") or {}
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        points = [
            [int(t), round(float(p), 4)]
            for t, p in zip(res.get("timestamp") or [], closes, strict=False)
            if isinstance(t, int) and _num(p) is not None
        ]
        dividends = [
            {"at": int(d["date"]), "amount": float(d["amount"])}
            for d in ((res.get("events") or {}).get("dividends") or {}).values()
            if isinstance(d.get("date"), int) and _num(d.get("amount")) is not None
        ]
        return {
            "name": meta.get("longName") or meta.get("shortName"),
            "currency": meta.get("currency"),
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "type": meta.get("instrumentType"),
            "price": _num(meta.get("regularMarketPrice")),
            # The close before the range starts: for "1d", yesterday's close.
            "previous_close": _num(meta.get("chartPreviousClose")),
            "price_at": meta.get("regularMarketTime"),
            "day_low": _num(meta.get("regularMarketDayLow")),
            "day_high": _num(meta.get("regularMarketDayHigh")),
            "year_low": _num(meta.get("fiftyTwoWeekLow")),
            "year_high": _num(meta.get("fiftyTwoWeekHigh")),
            "volume": _num(meta.get("regularMarketVolume")),
            "points": points,
            "dividends": dividends,
        }

    return _cached(("chart", symbol, range_), seconds, load)


def dividend_yield(symbol: str, price: float | None) -> float | None:
    """The last 12 months of dividends over today's price, in percent."""
    year = chart(symbol, "1y")
    if not year or not price:
        return None
    since = (datetime.now(UTC) - timedelta(days=365)).timestamp()
    paid = sum(d["amount"] for d in year["dividends"] if d["at"] >= since)
    return round(paid / price * 100, 2) if paid else None


def profile(settings: Settings, symbol: str) -> dict:
    """Company numbers from Finnhub (with a key). Empty without one, or for funds Finnhub doesn't cover."""
    if not settings.finnhub_api_key:
        return {}

    def load() -> dict:
        headers = {"X-Finnhub-Token": settings.finnhub_api_key}
        out: dict = {}
        with _client() as c:
            p = c.get("https://finnhub.io/api/v1/stock/profile2", params={"symbol": symbol}, headers=headers)
            if p.status_code == 200 and isinstance(p.json(), dict):
                d = p.json()
                cap = _num(d.get("marketCapitalization"))
                out.update(
                    {
                        "industry": d.get("finnhubIndustry") or None,
                        "market_cap": cap * 1_000_000 if cap else None,  # Finnhub gives millions
                        "country": d.get("country") or None,
                        "ipo": d.get("ipo") or None,
                    }
                )
            m = c.get(
                "https://finnhub.io/api/v1/stock/metric", params={"symbol": symbol, "metric": "all"}, headers=headers
            )
            if m.status_code == 200 and isinstance(m.json(), dict):
                metric = m.json().get("metric") or {}
                out.update(
                    {
                        "pe": _num(metric.get("peTTM") or metric.get("peBasicExclExtraTTM")),
                        "beta": _num(metric.get("beta")),
                        "eps": _num(metric.get("epsTTM") or metric.get("epsBasicExclExtraItemsTTM")),
                    }
                )
        return {k: v for k, v in out.items() if v is not None}

    try:
        return _cached(("profile", symbol), PROFILE_SECONDS, load)
    except (httpx.HTTPError, ValueError) as e:
        log.info("finnhub profile for a symbol failed (%s)", type(e).__name__)
        return {}
