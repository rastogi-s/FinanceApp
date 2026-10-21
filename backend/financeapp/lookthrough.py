"""Look-through exposure: your real share of every company, counting what sits inside your funds.

AAPL's share = (AAPL held directly + for each fund, your money in the fund x AAPL's weight in it) / everything
invested. Example: 3% of the portfolio in AAPL directly plus 20% in VOO, where AAPL is 7% of VOO, is 4.4%.

Where fund holdings come from. Each fund's latest public N-PORT filing at the SEC: official, free, covering ETFs
and mutual funds (Fidelity's too), filed quarterly and about 60 days behind; every number shows its as-of date.
A few big ETFs are unit trusts that file no N-PORT (SPY); they borrow the holdings of a fund tracking the same
index (IVV), and say so. Funds inside funds (target-date funds) are opened up one more level.

Filings name securities by CUSIP and name, rarely by ticker. The ticker and company come from the filing when it
has one, then a CUSIP looked up once at OpenFIGI (Bloomberg's free identifier service; only CUSIPs are sent),
then the company's name matched against the SEC's list of companies. Share classes of one company (GOOGL and
GOOG) count as one company.

What is sent where: the SEC gets fund series ids and a contact email (it asks every program for one), OpenFIGI
gets CUSIPs. Neither gets anything about the household's money. Filings are parsed with defusedxml and capped in
size, since they come from outside.
"""

import contextlib
import logging
import re
import time as clock
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import BinaryIO

import httpx
from defusedxml import ElementTree
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import portfolio
from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import Account, Fund, FundConstituent, HoldingSnapshot, SecurityId, User, now

log = logging.getLogger("financeapp")
REFRESH_DAYS = 7  # N-PORT is quarterly; a weekly look is plenty
NOT_FOUND_RETRY_DAYS = 30
MAX_FILING_BYTES = 150 * 1024 * 1024  # the biggest bond funds' filings are tens of MB
EQUITY = {"EC", "EP"}  # common and preferred stock
FUND = "RF"  # a registered fund held inside the fund
NESTED_MIN_WEIGHT = Decimal("0.01")  # open up a fund inside a fund when it's at least 1% of it
FIGI_BATCH = 10  # OpenFIGI without a key: 10 ids a request, 25 requests a minute
FIGI_MAX_REQUESTS = 20  # per fund per refresh; the rest are matched by name
FIGI_PAUSE = 2.6
SEC_PAUSE = 0.15  # the SEC allows 10 requests a second
REFERENCE_HOURS = 24
# Unit trusts that file no N-PORT, and a fund that tracks the same index.
PROXIES = {"SPY": "IVV", "MDY": "IJH"}
SUFFIXES = {
    "inc", "corp", "corporation", "co", "cos", "company", "companies", "ltd", "plc", "the", "holdings", "holding",
    "group", "sa", "nv", "ag", "se", "lp", "llc", "incorporated", "limited", "de", "new", "class", "cl", "com", "shs",
    "a", "b", "c",
}  # fmt: skip


def normalize(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " and ")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


# ---- reference lists from the SEC (which funds exist, which companies have which tickers), kept a day


class Reference:
    def __init__(self, funds: dict[str, tuple[int, str]], companies: list[dict]) -> None:
        self.funds = funds  # ticker -> (trust cik, series id)
        self.cik_of: dict[str, int] = {}  # ticker -> company cik
        self.ticker_of: dict[int, str] = {}  # cik -> its main ticker (the SEC lists the main one first)
        self.title_of: dict[int, str] = {}
        by_name: dict[str, set[int]] = defaultdict(set)
        for c in companies:
            cik, ticker = int(c["cik_str"]), str(c["ticker"]).upper()
            self.cik_of.setdefault(ticker, cik)
            self.ticker_of.setdefault(cik, ticker)
            self.title_of.setdefault(cik, c["title"])
            by_name[normalize(c["title"])].add(cik)
        self.cik_by_name = {n: next(iter(ciks)) for n, ciks in by_name.items() if len(ciks) == 1 and n}

    def company(self, ticker: str) -> int | None:
        t = ticker.upper()
        return self.cik_of.get(t) or self.cik_of.get(t.replace(".", "-")) or self.cik_of.get(t.replace("-", "."))


_reference: tuple[datetime, Reference] | None = None


def user_agent(db: DbSession, settings: Settings) -> str | None:
    email = settings.sec_contact_email
    if not email:
        email = db.scalar(select(User.email).where(User.role == "owner").order_by(User.id).limit(1))
    return f"FinanceApp self-hosted personal finance {email}" if email else None


def reference(client: httpx.Client) -> Reference:
    global _reference
    if _reference and _reference[0] > now() - timedelta(hours=REFERENCE_HOURS):
        return _reference[1]
    mf = client.get("https://www.sec.gov/files/company_tickers_mf.json")
    mf.raise_for_status()
    data = mf.json()
    fields = data["fields"]
    i_cik, i_series, i_symbol = fields.index("cik"), fields.index("seriesId"), fields.index("symbol")
    funds = {str(r[i_symbol]).upper(): (int(r[i_cik]), str(r[i_series])) for r in data["data"] if r[i_symbol]}
    clock.sleep(SEC_PAUSE)
    co = client.get("https://www.sec.gov/files/company_tickers.json")
    co.raise_for_status()
    ref = Reference(funds, list(co.json().values()))
    _reference = (now(), ref)
    return ref


# ---- reading a filing


class Stream:
    """A file-like view of a download, so the XML parser reads it as it arrives, with a size cap."""

    def __init__(self, response: httpx.Response, limit: int) -> None:
        self.chunks = response.iter_bytes(64 * 1024)
        self.buffer = b""
        self.seen = 0
        self.limit = limit

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self.buffer) < n:
            try:
                chunk = next(self.chunks)
            except StopIteration:
                break
            self.seen += len(chunk)
            if self.seen > self.limit:
                raise ValueError("filing too large")
            self.buffer += chunk
        if n < 0:
            out, self.buffer = self.buffer, b""
        else:
            out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decimal(text: str | None) -> Decimal | None:
    try:
        d = Decimal((text or "").strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def parse_nport(source: BinaryIO) -> dict:
    """The parts of an N-PORT filing that matter here: the fund's name, the date it's as of, and each holding's
    name, ids, asset category and percentage of net assets (negative for short positions)."""
    out: dict = {"name": None, "as_of": None, "holdings": []}
    for _event, el in ElementTree.iterparse(source, events=("end",), forbid_dtd=True):
        tag = _local(el.tag)
        if tag == "seriesName":
            out["name"] = (el.text or "").strip()[:200] or None
        elif tag == "repPdDate":
            with contextlib.suppress(ValueError):
                out["as_of"] = date.fromisoformat((el.text or "").strip())
        elif tag == "invstOrSec":
            h: dict = {"ticker": None, "isin": None}
            for child in el.iter():
                t = _local(child.tag)
                if t in ("name", "title", "cusip", "pctVal", "assetCat", "payoffProfile"):
                    h[t] = (child.text or "").strip()
                elif t == "ticker":
                    h["ticker"] = (child.get("value") or "").strip().upper() or None
                elif t == "isin":
                    h["isin"] = (child.get("value") or "").strip() or None
                elif t == "assetConditional":  # "other" categories put theirs in an attribute
                    h.setdefault("assetCat", child.get("assetCat") or "")
            pct = _decimal(h.get("pctVal"))
            if pct is not None:
                if h.get("payoffProfile") == "Short":
                    pct = -abs(pct)
                out["holdings"].append(
                    {
                        "name": "" if (h.get("name") or "").upper() == "N/A" else (h.get("name") or ""),
                        "title": h.get("title") or "",
                        "cusip": h.get("cusip") if re.fullmatch(r"[0-9A-Z]{9}", h.get("cusip") or "") else None,
                        "ticker": h["ticker"],
                        "category": h.get("assetCat") or "",
                        "weight": pct / 100,
                    }
                )
            el.clear()
    return out


# ---- ids


def figi_lookup(client: httpx.Client, cusips: list[str]) -> dict[str, str | None]:
    """CUSIP -> US ticker (or None if OpenFIGI doesn't know it). Stops quietly at a rate limit."""
    out: dict[str, str | None] = {}
    for i in range(0, min(len(cusips), FIGI_BATCH * FIGI_MAX_REQUESTS), FIGI_BATCH):
        batch = cusips[i : i + FIGI_BATCH]
        if i:
            clock.sleep(FIGI_PAUSE)
        r = client.post(
            "https://api.openfigi.com/v3/mapping",
            json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch],
        )
        if r.status_code == 429:
            break
        r.raise_for_status()
        for cusip, result in zip(batch, r.json(), strict=False):
            data = result.get("data") or []
            ticker = str(data[0].get("ticker") or "").upper() if data else ""
            out[cusip] = ticker.replace("/", ".") if re.fullmatch(r"[A-Z][A-Z0-9/.\-]{0,9}", ticker) else None
    return out


def identify(db: DbSession, client: httpx.Client | None, ref: Reference, holdings: list[dict]) -> None:
    """Gives each holding a key (its company if known), ticker and display name."""
    cusips = {h["cusip"] for h in holdings if h["cusip"]}
    known = {s.cusip: s for s in db.scalars(select(SecurityId).where(SecurityId.cusip.in_(cusips)))} if cusips else {}
    stale = now() - timedelta(days=NOT_FOUND_RETRY_DAYS)

    def resolve(h: dict) -> tuple[str | None, int | None]:
        if h["ticker"] and ref.company(h["ticker"]):
            return h["ticker"], ref.company(h["ticker"])
        s = known.get(h["cusip"]) if h["cusip"] else None
        if s and s.ticker:
            return s.ticker, s.cik
        cik = ref.cik_by_name.get(normalize(h["name"])) or ref.cik_by_name.get(normalize(h.get("title") or ""))
        if cik:
            return ref.ticker_of.get(cik), cik
        if s and s.resolved_at <= stale:
            known.pop(h["cusip"])  # not found a month ago: ask OpenFIGI again
        return h["ticker"], None

    for h in holdings:
        h["symbol"], h["cik"] = resolve(h)
    # The rest, largest first, through OpenFIGI (once per CUSIP; misses are remembered and tried again in a month).
    if client is not None:
        todo = sorted(
            (h for h in holdings if h["cik"] is None and h["cusip"] and h["cusip"] not in known),
            key=lambda h: -abs(h["weight"]),
        )
        found: dict[str, str | None] = {}
        try:
            found = figi_lookup(client, list(dict.fromkeys(h["cusip"] for h in todo)))
        except (httpx.HTTPError, ValueError) as e:
            log.info("OpenFIGI lookup stopped (%s); the rest are matched by name", type(e).__name__)
        for cusip, ticker in found.items():
            cik = ref.company(ticker) if ticker else None
            db.merge(SecurityId(cusip=cusip, ticker=ticker, cik=cik, resolved_at=now()))
        for h in todo:
            if h["cusip"] in found:
                h["symbol"] = found[h["cusip"]] or h["symbol"]
                h["cik"] = ref.company(h["symbol"]) if h["symbol"] else None
        for s_cusip, ticker in found.items():
            known[s_cusip] = SecurityId(cusip=s_cusip, ticker=ticker)
    for h in holdings:
        if h["cik"]:
            h["key"] = f"cik:{h['cik']}"
            h["symbol"] = ref.ticker_of.get(h["cik"], h["symbol"])
        elif h["symbol"]:
            h["key"] = f"ticker:{h['symbol']}"
        elif h["cusip"]:
            h["key"] = f"cusip:{h['cusip']}"
        else:
            h["key"] = f"name:{normalize(h['name'])[:70] or 'unknown'}"
        h["display"] = h["name"] or (ref.title_of.get(h["cik"]) if h["cik"] else None) or h.get("title") or "?"


# ---- refreshing funds


def held(db: DbSession) -> list[tuple[str, str]]:
    """(symbol, asset class) of everything in the latest holdings of counted accounts."""
    latest = (
        select(HoldingSnapshot.account_id, func.max(HoldingSnapshot.date).label("d"))
        .group_by(HoldingSnapshot.account_id)
        .subquery()
    )
    return list(
        db.execute(
            select(HoldingSnapshot.symbol, HoldingSnapshot.asset_class)
            .join(latest, (HoldingSnapshot.account_id == latest.c.account_id) & (HoldingSnapshot.date == latest.c.d))
            .join(Account, Account.id == HoldingSnapshot.account_id)
            .where(Account.hidden.is_(False), Account.is_authoritative.is_(True))
            .distinct()
        ).all()
    )


def latest_filing(client: httpx.Client, series_id: str) -> tuple[str, str] | None:
    """The newest public N-PORT for a fund series: (accession number, primary document URL)."""
    r = client.get(
        "https://www.sec.gov/cgi-bin/browse-edgar",
        params={"action": "getcompany", "CIK": series_id, "type": "NPORT-P", "dateb": "", "owner": "include",
                "count": "5", "output": "atom"},
    )  # fmt: skip
    r.raise_for_status()
    root = ElementTree.fromstring(r.content, forbid_dtd=True)
    for el in root.iter():
        if _local(el.tag) != "entry":
            continue
        kind = href = None
        for child in el.iter():
            name = _local(child.tag)
            if name == "filing-type":
                kind = (child.text or "").strip()
            elif name == "filing-href":
                href = (child.text or "").strip()
        if kind == "NPORT-P" and href:
            m = re.fullmatch(r"https://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/[\w.-]+-index\.html?", href)
            if m:
                return m.group(2), f"https://www.sec.gov/Archives/edgar/data/{m.group(1)}/{m.group(2)}/primary_doc.xml"
    return None


def save_fund(db: DbSession, fund: Fund, parsed: dict, accession: str) -> list[str]:
    """Replaces a fund's constituents. Returns funds inside it worth opening up too."""
    rows: dict[str, FundConstituent] = {}
    nested = []
    equity = Decimal(0)
    for h in parsed["holdings"]:
        is_fund = h["category"] == FUND
        if h["category"] not in EQUITY and not is_fund:
            continue
        if not is_fund:
            equity += h["weight"]
        row = rows.get(h["key"])
        if row is None:
            rows[h["key"]] = FundConstituent(
                fund_symbol=fund.symbol, key=h["key"][:80], symbol=(h["symbol"] or None) and h["symbol"][:40],
                name=(h["display"] or h["name"] or "?")[:200], weight=Decimal(0), is_fund=is_fund,
            )  # fmt: skip
            row = rows[h["key"]]
        row.weight += h["weight"]
        if is_fund and h["symbol"] and h["weight"] >= NESTED_MIN_WEIGHT:
            nested.append(h["symbol"])
    db.execute(delete(FundConstituent).where(FundConstituent.fund_symbol == fund.symbol))
    db.add_all(r for r in rows.values() if r.weight != 0)
    fund.name = parsed["name"] or fund.name
    fund.as_of = parsed["as_of"]
    fund.accession = accession
    fund.equity_weight = equity
    fund.source = "nport"
    fund.status = "ok"
    fund.error = None
    return nested


def refresh_fund(db: DbSession, client: httpx.Client, ref: Reference, symbol: str) -> list[str]:
    fund = db.get(Fund, symbol) or Fund(symbol=symbol, source="none", status="new")
    db.add(fund)
    fund.fetched_at = now()
    if symbol in PROXIES:
        fund.source, fund.proxy_of, fund.status, fund.error = "proxy", PROXIES[symbol], "ok", None
        return [PROXIES[symbol]]
    if symbol not in ref.funds:
        fund.status, fund.error = "not_found", "The SEC has no fund with this ticker."
        return []
    fund.series_id = ref.funds[symbol][1]
    filing = latest_filing(client, fund.series_id)
    clock.sleep(SEC_PAUSE)
    if filing is None:
        fund.status, fund.error = "not_found", "No public holdings filing yet."
        return []
    accession, url = filing
    if accession == fund.accession and fund.status == "ok":
        return []
    with client.stream("GET", url) as r:
        r.raise_for_status()
        parsed = parse_nport(Stream(r, MAX_FILING_BYTES))
    identify(db, client, ref, parsed["holdings"])
    return save_fund(db, fund, parsed, accession)


def refresh(database: Database, settings: Settings, force: bool = False) -> dict:
    """Brings every held fund's holdings up to date (each at most weekly). Returns what happened, per fund."""
    if not settings.lookthrough_enabled:
        return {}
    with database.session() as db:
        agent = user_agent(db, settings)
        symbols = sorted({s for s, _ in held(db)})
        cutoff = now() - timedelta(days=REFRESH_DAYS)
        fresh = {
            f.symbol
            for f in db.scalars(select(Fund).where(Fund.fetched_at > cutoff))
            if not force and f.status in ("ok", "not_found")
        }
    if agent is None or not symbols:
        return {}
    results: dict[str, str] = {}
    with httpx.Client(timeout=60, headers={"User-Agent": agent}, follow_redirects=False) as client:
        try:
            ref = reference(client)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("couldn't load the SEC's fund list (%s)", type(e).__name__)
            return {}
        todo = [s for s in symbols if (s in ref.funds or s in PROXIES) and s not in fresh]
        done: set[str] = set()
        while todo:
            symbol = todo.pop(0)
            if symbol in done:
                continue
            done.add(symbol)
            with database.session() as db:
                try:
                    nested = refresh_fund(db, client, ref, symbol)
                    results[symbol] = db.get_one(Fund, symbol).status
                except Exception as e:  # one fund failing never stops the others
                    db.rollback()
                    log.warning("fund holdings for %s failed (%s)", symbol, type(e).__name__)
                    f = db.get(Fund, symbol) or Fund(symbol=symbol, source="none")
                    f.status, f.fetched_at = "error", now()
                    f.error = "Couldn't read the fund's filing; trying again later."
                    db.merge(f)
                    results[symbol] = "error"
                    continue
            if len(done) < 40:  # nested funds, one level down (plus proxies' stand-ins)
                todo.extend(n for n in nested if n not in done and n not in fresh)
    return results


# ---- exposure


def exposure(db: DbSession, accounts: list[Account]) -> dict:
    ps = portfolio.positions(db, accounts)
    total = sum(p.value_cents for p in ps)
    funds = {f.symbol: f for f in db.scalars(select(Fund))}
    cons: dict[str, list[FundConstituent]] = defaultdict(list)
    for c in db.scalars(select(FundConstituent)):
        cons[c.fund_symbol].append(c)
    companies: dict[str, dict] = {}
    fund_rows: dict[str, dict] = {}
    other = 0.0

    def source_of(symbol: str) -> str | None:
        f = funds.get(symbol)
        if f is None or f.status != "ok":
            return None
        src = f.proxy_of if f.source == "proxy" else symbol
        return src if cons.get(src or "") else None

    def add(key: str, symbol: str | None, name: str, cents: float, via: str) -> None:
        c = companies.setdefault(key, {"key": key, "symbol": symbol, "name": name, "cents": 0.0, "sources": {}})
        c["cents"] += cents
        c["sources"][via] = c["sources"].get(via, 0.0) + cents
        if c["name"].isupper() and not name.isupper():
            c["name"] = name  # funds write names differently; "Microsoft Corp" reads better than "MICROSOFT CORP"
        if symbol and (not c["symbol"] or via == "direct"):
            c["symbol"] = symbol  # a stock held directly shows the way the brokerage writes it

    def spread(symbol: str, cents: float, via: str, depth: int) -> float:
        """Adds a fund's companies; returns how much of `cents` went to companies."""
        src = source_of(symbol)
        if src is None:
            return 0.0
        placed = 0.0
        for c in cons[src]:
            amount = cents * float(c.weight)
            if c.is_fund:
                if depth < 2 and c.symbol:
                    placed += spread(c.symbol, amount, via, depth + 1)
                continue
            add(c.key, c.symbol, c.name, amount, via)
            placed += amount
        return placed

    ref = _reference[1] if _reference else None
    key_of = {}
    for c in (c for rows in cons.values() for c in rows if c.symbol and not c.is_fund):
        for form in (c.symbol, c.symbol.replace("-", "."), c.symbol.replace(".", "-")):
            key_of.setdefault(form, c.key)
    for p in ps:
        if source_of(p.symbol) is not None:
            placed = spread(p.symbol, p.value_cents, p.symbol, 0)
            row = fund_rows.setdefault(p.symbol, {"symbol": p.symbol, "name": p.name, "cents": 0, "placed": 0.0})
            row["cents"] += p.value_cents
            row["placed"] += placed
        elif p.symbol in funds or p.asset_class in ("Funds", "Bonds"):
            other += p.value_cents
            row = fund_rows.setdefault(p.symbol, {"symbol": p.symbol, "name": p.name, "cents": 0, "placed": 0.0})
            row["cents"] += p.value_cents
        elif p.asset_class == "Stocks":
            # The same company inside a fund has its key already; else the SEC's list, if it's loaded.
            cik = ref.company(p.symbol) if ref else None
            key = key_of.get(p.symbol) or (f"cik:{cik}" if cik else f"ticker:{p.symbol}")
            add(key, p.symbol, p.name, p.value_cents, "direct")
        else:
            other += p.value_cents

    def pct(cents: float) -> float:
        return round(cents / total * 100, 3) if total else 0.0

    rows = sorted(companies.values(), key=lambda c: -c["cents"])
    out_companies = [
        {
            "key": c["key"],
            "symbol": c["symbol"],
            "name": c["name"],
            "cents": round(c["cents"]),
            "percent": pct(c["cents"]),
            "direct_percent": pct(c["sources"].get("direct", 0.0)),
            "sources": sorted(
                ({"symbol": s, "cents": round(v), "percent": pct(v)} for s, v in c["sources"].items()),
                key=lambda s: -s["cents"],
            ),
        }
        for c in rows
        if c["cents"] > 0
    ]
    out_funds = []
    for symbol, row in sorted(fund_rows.items(), key=lambda kv: -kv[1]["cents"]):
        f = funds.get(symbol)
        src = source_of(symbol)
        sf = funds.get(src) if src else None
        top = sorted((c for c in cons.get(src or "", []) if not c.is_fund), key=lambda c: -c.weight)[:10]
        equity = round(float(sf.equity_weight) * 100, 1) if sf and sf.equity_weight is not None else None
        out_funds.append(
            {
                "symbol": symbol,
                "name": (sf.name if sf and sf.name and f and f.source != "proxy" else None) or row["name"],
                "cents": row["cents"],
                "percent": pct(row["cents"]),
                "status": "ok" if src else (f.status if f else "new"),
                "error": f.error if f else None,
                "proxy_of": f.proxy_of if f and f.source == "proxy" else None,
                "as_of": sf.as_of.isoformat() if sf and sf.as_of else None,
                "holdings": len(cons.get(src or "", [])),
                "equity_percent": equity,
                "top": [
                    {"symbol": c.symbol, "name": c.name, "weight_percent": round(float(c.weight) * 100, 2)} for c in top
                ],
            }
        )
    seen = sum(r["cents"] for r in fund_rows.values() if source_of(r["symbol"]))
    as_ofs = [f["as_of"] for f in out_funds if f["as_of"]]
    return {
        "total_cents": total,
        "companies": out_companies,
        "funds": out_funds,
        "looked_through_percent": round(seen / total * 100, 1) if total else 0.0,
        "other_percent": pct(other),
        "oldest_as_of": min(as_ofs) if as_ofs else None,
    }
