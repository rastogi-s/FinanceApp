"""The portfolio: positions across accounts, look-through exposure, and the detail page of each holding."""

import logging
import threading
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import demo, household, lookthrough, lots, portfolio, quotes, security
from financeapp.ai import moves, research
from financeapp.ai.providers import ProviderError
from financeapp.ai.service import AIUnavailable
from financeapp.api.alert_routes import rule_json
from financeapp.api.deps import Signed, audit, settings_of, signed_in
from financeapp.models import AlertRule, Category, Fund, Preference, Quote, now

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


# The Portfolio page shows the top 100 companies (the most the graph draws); the rest are found by searching.
TOP_COMPANIES = 100


def light(exposure: dict) -> dict:
    """Look-through for the page: thousands of companies is megabytes, and the page shows the largest only."""
    return {**exposure, "companies": exposure["companies"][:TOP_COMPANIES], "company_count": len(exposure["companies"])}


def trends(db: DbSession, symbols: set[str]) -> dict:
    """Each held symbol's year range, 50- and 200-day averages and last month of closes, for the holdings table."""
    out = {}
    for s, q in quotes.latest(db, symbols).items():
        out[s] = {
            "previous_close": float(q.previous_close) if q.previous_close else None,
            "year_low": float(q.year_low) if q.year_low else None,
            "year_high": float(q.year_high) if q.year_high else None,
            "average_50": float(q.average_50) if q.average_50 else None,
            "average_200": float(q.average_200) if q.average_200 else None,
            "closes": q.closes or [],
        }
    return out


@router.get("/portfolio")
def get_portfolio(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        p = demo.portfolio()
        return {**p, "exposure": light(p["exposure"])}
    db = db_of(request)
    accounts = household.counted(db)
    names = {a.id: household.display_name(a) for a in accounts}
    ps = portfolio.positions(db, accounts)
    as_of = portfolio.prices_as_of(ps)
    return {
        **portfolio.combined(ps, names),
        "exposure": light(lookthrough.exposure(db, accounts, ps)),
        "trends": trends(db, {p.symbol for p in ps if quotes.quotable(p.symbol, p.asset_class)}),
        "prices_as_of": as_of.isoformat() if as_of else None,
        "market_open": quotes.market_open(now()),
        "sources": sources(request, db),
    }


@router.get("/portfolio/companies")
def find_companies(
    request: Request, q: str = Query(min_length=1, max_length=60), who: Signed = Depends(signed_in)
) -> dict:
    """Search every company you own, directly or through funds, by ticker or name. Each keeps its overall rank."""
    if who.demo:
        companies = demo.portfolio()["exposure"]["companies"]
    else:
        db = db_of(request)
        companies = lookthrough.exposure(db, household.counted(db))["companies"]
    needle = q.strip().lower()
    found = [
        {**c, "rank": i + 1}
        for i, c in enumerate(companies)
        if (c["symbol"] or "").lower().startswith(needle) or needle in c["name"].lower()
    ]
    return {"companies": found[:50], "count": len(found)}


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


@router.get("/portfolio/moves")
def moves_offer(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """What "Any moves today?" would send, to whom and at most what cost, and the last answer. Nothing is sent."""
    if who.demo:
        return demo.moves_offer()
    return moves.offer(db_of(request), settings_of(request), who.user.id)


@router.get("/portfolio/moves/last")
def moves_last(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Only the last answer, for the Portfolio page's card (the offer looks up the market, this doesn't)."""
    if who.demo:
        return {"last": demo.moves_offer()["last"]}
    return {"last": moves.last(db_of(request))}


class MovesIn(BaseModel):
    question: str = Field("", max_length=moves.MAX_QUESTION)


@router.post("/portfolio/moves")
def moves_review(body: MovesIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """A second opinion from AI on buying, selling or holding. Ideas only: nothing is traded."""
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    try:
        result = moves.review(request.app.state.db, db, settings_of(request), who.user.id, body.question)
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, e.message) from None
    except moves.NothingToReview as e:
        raise HTTPException(422, e.message) from None
    count = len(result["answer"]["moves"])
    audit(db, request, "portfolio_ai_reviewed", who.user.id, model=result["model"], moves=count)
    return result


def ticker(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not security.valid(symbol):
        raise HTTPException(404, "Not a ticker symbol.")
    return symbol


@router.get("/securities/{symbol}/ask")
def research_offer(symbol: str, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Ask AI on a holding's page: what would be sent, to whom, at most what cost, and the kept conversation."""
    symbol = ticker(symbol)
    if who.demo:
        return demo.research_offer(symbol)
    return research.offer(db_of(request), settings_of(request), who.user.id, symbol)


class AskIn(BaseModel):
    question: str = Field("", max_length=research.MAX_QUESTION)


@router.post("/securities/{symbol}/ask")
def research_ask(symbol: str, body: AskIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Researches the ticker (news and, with Claude, the web) and answers. An educated take, not advice."""
    symbol = ticker(symbol)
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    try:
        result = research.ask(request.app.state.db, db, settings_of(request), who.user.id, symbol, body.question)
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, e.message) from None
    except research.NotAvailable as e:
        raise HTTPException(422, e.message) from None
    entry = result["entry"]
    audit(
        db, request, "ticker_researched", who.user.id, symbol=symbol, model=entry["model"], searches=entry["searched"]
    )
    return result


@router.delete("/securities/{symbol}/ask")
def research_forget(symbol: str, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Clears the kept questions and answers for this ticker."""
    symbol = ticker(symbol)
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    research.forget(db_of(request), symbol)
    return {"thread": []}


@router.get("/securities/{symbol}")
def detail(
    symbol: str,
    request: Request,
    range_: Literal["1d", "5d", "1m", "6m", "1y", "5y"] = Query("1d", alias="range"),
    who: Signed = Depends(signed_in),
) -> dict:
    symbol = ticker(symbol)
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
        rows = lots.of(db, accounts, mine)
        if rows:
            position["lots"] = {"rows": rows, **lots.summary(rows)}
    locked = portfolio.unvested(db, accounts, symbol)
    if locked and price:
        if position is None:
            position = {
                "quantity": 0.0, "value_cents": 0, "cost_basis_cents": None, "gain_cents": None, "gain_percent": None,
                "percent_of_portfolio": None, "accounts": [], "as_of": None,
            }  # fmt: skip
        count = float(sum(locked.values()))
        position["unvested"] = {
            "quantity": count,
            "value_cents": round(count * price * 100),
            "accounts": [{"account": names.get(aid, ""), "quantity": float(q)} for aid, q in locked.items()],
        }

    look = lookthrough.exposure(db, accounts, ps) if mine or db.get(Fund, symbol) else None
    company = next((c for c in (look or {}).get("companies", []) if c["symbol"] == symbol), None)
    fund = next((f for f in (look or {}).get("funds", []) if f["symbol"] == symbol), None)
    categories = dict(db.execute(select(Category.id, Category.name)).all())
    rules = [
        rule_json(r, categories)
        for r in db.scalars(select(AlertRule).where(AlertRule.kind.in_(["price", "dip"])))
        if r.params.get("symbol") == symbol
    ]
    trend = db.get(Quote, symbol)
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
            "average_50": float(trend.average_50) if trend and trend.average_50 else None,
            "average_200": float(trend.average_200) if trend and trend.average_200 else None,
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
