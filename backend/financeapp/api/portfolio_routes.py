"""The portfolio: positions across accounts, look-through exposure, and the detail page of each holding."""

import logging
import threading
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import demo, household, lookthrough, portfolio, quotes, security
from financeapp.api.alert_routes import rule_json
from financeapp.api.deps import Signed, audit, settings_of, signed_in
from financeapp.models import AlertRule, Category, Fund, Preference, now

log = logging.getLogger("financeapp")
router = APIRouter(prefix="/api")
REFRESH_EVERY = timedelta(minutes=1)
_refreshing = threading.Lock()


def db_of(request: Request) -> DbSession:
    return request.state.db


def sources(request: Request, db: DbSession) -> dict:
    settings = settings_of(request)
    provider = settings.quotes_provider.lower()
    quotes_from = "finnhub" if settings.finnhub_api_key and provider != "yahoo" else "yahoo"
    return {
        "quotes": "off" if provider == "off" else quotes_from,
        "lookthrough": settings.lookthrough_enabled and lookthrough.user_agent(db, settings) is not None,
        "fundamentals": bool(settings.finnhub_api_key),
    }


@router.get("/portfolio")
def get_portfolio(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.portfolio()
    db = db_of(request)
    accounts = household.counted(db)
    names = {a.id: household.display_name(a) for a in accounts}
    ps = portfolio.positions(db, accounts)
    as_of = portfolio.prices_as_of(ps)
    return {
        **portfolio.combined(ps, names),
        "exposure": lookthrough.exposure(db, accounts),
        "prices_as_of": as_of.isoformat() if as_of else None,
        "market_open": quotes.market_open(now()),
        "sources": sources(request, db),
    }


@router.post("/portfolio/refresh")
def refresh(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Fresh prices now, and fund holdings looked up again in the background (that can take a few minutes)."""
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    p = db.get(Preference, "portfolio_refresh") or Preference(key="portfolio_refresh", value={})
    last = p.value.get("at")
    if last and now() - datetime.fromisoformat(last) < REFRESH_EVERY:
        raise HTTPException(429, "Prices were just updated. Try again in a minute.")
    p.value = {"at": now().isoformat()}
    db.merge(p)
    audit(db, request, "portfolio_refresh", who.user.id)
    db.commit()
    database, settings = request.app.state.db, settings_of(request)
    count = quotes.refresh(database, settings, force=True)

    def funds() -> None:
        if not _refreshing.acquire(blocking=False):
            return
        try:
            lookthrough.refresh(database, settings, force=False)
        except Exception:
            log.exception("fund holdings refresh failed")
        finally:
            _refreshing.release()

    threading.Thread(target=funds, name="lookthrough-refresh", daemon=True).start()
    return {"quotes": count or 0}


@router.get("/securities/{symbol}")
def detail(
    symbol: str,
    request: Request,
    range_: Literal["1d", "5d", "1m", "6m", "1y", "5y"] = Query("1d", alias="range"),
    who: Signed = Depends(signed_in),
) -> dict:
    symbol = symbol.strip().upper()
    if not security.valid(symbol):
        raise HTTPException(404, "Not a ticker symbol.")
    if who.demo:
        return demo.security(symbol, range_)
    db = db_of(request)
    settings = settings_of(request)
    accounts = household.counted(db)
    names = {a.id: household.display_name(a) for a in accounts}
    ps = portfolio.positions(db, accounts)
    mine = [p for p in ps if p.symbol == symbol]
    total = sum(p.value_cents for p in ps)

    market = None
    points = None
    if settings.quotes_provider.lower() != "off":
        try:
            market = security.chart(symbol, "1d")
            if market and range_ == "1d":
                points = market["points"]
            else:
                points = (security.chart(symbol, range_) or {}).get("points")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.info("market data for a symbol failed (%s)", type(e).__name__)  # the page shows what it has
    if not mine and market is None:
        raise HTTPException(404, "Couldn't find that symbol.")

    price = (market or {}).get("price") or (float(mine[0].price) if mine else None)
    previous = (market or {}).get("previous_close")
    position = None
    if mine:
        value = sum(p.value_cents for p in mine)
        basis = [p.cost_basis_cents for p in mine]
        cost = sum(basis) if all(b is not None for b in basis) else None  # type: ignore[arg-type]
        position = {
            "quantity": float(sum(p.quantity for p in mine)),
            "value_cents": value,
            "cost_basis_cents": cost,
            "gain_cents": value - cost if cost else None,
            "gain_percent": round((value - cost) / cost * 100, 2) if cost else None,
            "percent_of_portfolio": round(value / total * 100, 2) if total else None,
            "accounts": [
                {"account": names.get(p.account_id, ""), "quantity": float(p.quantity), "value_cents": p.value_cents}
                for p in sorted(mine, key=lambda p: -p.value_cents)
            ],
            "as_of": max(p.as_of for p in mine).isoformat(),
        }

    look = lookthrough.exposure(db, accounts) if mine or db.get(Fund, symbol) else None
    company = next((c for c in (look or {}).get("companies", []) if c["symbol"] == symbol), None)
    fund = next((f for f in (look or {}).get("funds", []) if f["symbol"] == symbol), None)
    categories = dict(db.execute(select(Category.id, Category.name)).all())
    rules = [
        rule_json(r, categories)
        for r in db.scalars(select(AlertRule).where(AlertRule.kind == "price"))
        if r.params.get("symbol") == symbol
    ]
    is_fund = fund is not None or (market or {}).get("type") in ("ETF", "MUTUALFUND")
    return {
        "symbol": symbol,
        "name": (market or {}).get("name") or (mine[0].name if mine else symbol),
        "type": (market or {}).get("type") or (mine[0].asset_class if mine else None),
        "is_fund": is_fund,
        "exchange": (market or {}).get("exchange"),
        "currency": (market or {}).get("currency") or "USD",
        "price": price,
        "previous_close": previous,
        "change_percent": round((price - previous) / previous * 100, 2) if price and previous else None,
        "price_at": (market or {}).get("price_at"),
        "range": range_,
        "points": points or [],
        "stats": {
            "day_low": (market or {}).get("day_low"),
            "day_high": (market or {}).get("day_high"),
            "year_low": (market or {}).get("year_low"),
            "year_high": (market or {}).get("year_high"),
            "volume": (market or {}).get("volume"),
            "dividend_yield": _safe(lambda: security.dividend_yield(symbol, price)) if market else None,
            **({} if is_fund else _safe(lambda: security.profile(settings, symbol)) or {}),
        },
        "position": position,
        "exposure": company,
        "fund": fund,
        "rules": rules,
        "market_data": market is not None,
        "sources": sources(request, db),
    }


def _safe(f):
    try:
        return f()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
