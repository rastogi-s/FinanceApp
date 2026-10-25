"""The household's money: accounts and connections, transactions and categories, file imports, and the sync."""

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import auth, crypto, dedupe, demo, household, notify, paystub, reminders, review, search, sync
from financeapp.ai import documents, extract, reading
from financeapp.ai.providers import ProviderError
from financeapp.ai.service import AIUnavailable
from financeapp.api.deps import Signed, audit, owner, recent, settings_of, signed_in
from financeapp.categorize import categorize
from financeapp.connectors.base import ConnectorError, HoldingData, TransactionData
from financeapp.connectors.files import MAX_BYTES, FileError, ParsedFile, parse
from financeapp.models import (
    Account,
    Balance,
    Category,
    Challenge,
    Connection,
    Import,
    SyncRun,
    Transaction,
    User,
    now,
)
from financeapp.transfers import match_transfers, unmatch

router = APIRouter(prefix="/api")
LINK_MINUTES = 30


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


# ---- household


@router.get("/household")
def get_household(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.household()
    return household.build(db_of(request))


# ---- accounts and connections


def connection_json(db: DbSession, c: Connection) -> dict:
    accounts = []
    due = {}
    if c.provider in reminders.PROVIDERS:
        rows = reminders.status(db, reminders.local_today(db))
        due = {r["account_id"]: r for r in rows if r["connection_id"] == c.id}
    for a in db.scalars(select(Account).where(Account.connection_id == c.id).order_by(Account.type, Account.name)):
        b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        match = None
        if a.statement_cents and b:
            match = round(abs(b.current_cents - a.statement_cents) / max(abs(a.statement_cents), 1) * 100, 2)
        accounts.append(
            {
                "id": a.id,
                "name": a.name,
                "display_name": household.display_name(a),
                "type": a.type,
                "subtype": a.subtype,
                "mask": a.mask,
                "hidden": a.hidden,
                "is_authoritative": a.is_authoritative,
                "owner_user_id": a.owner_user_id,
                "balance_cents": b.current_cents if b else None,
                "balance_date": b.date.isoformat() if b else None,
                "statement_cents": a.statement_cents,
                "statement_date": a.statement_date.isoformat() if a.statement_date else None,
                "statement_diff_percent": match,
                "reminder": reminder_json(due[a.id]) if a.id in due else None,
            }
        )
    return {
        "id": c.id,
        "provider": c.provider,
        "institution": c.institution,
        "status": c.status,
        "last_synced_at": c.last_synced_at.isoformat() if c.last_synced_at else None,
        "last_error": c.last_error,
        "created_at": c.created_at.isoformat(),
        "accounts": accounts,
    }


def reminder_json(r: dict) -> dict:
    keys = ("every", "day", "newest", "statement", "due", "is_due", "days_late", "snoozed_until")
    return {k: r[k] for k in keys}


@router.get("/connections")
def connections(request: Request, who: Signed = Depends(signed_in)) -> dict:
    settings = settings_of(request)
    if who.demo:
        return demo.connections()
    db = db_of(request)
    rows = db.scalars(select(Connection).where(Connection.status != "revoked").order_by(Connection.created_at))
    revoked = db.scalars(select(Connection).where(Connection.status == "revoked").order_by(Connection.created_at))
    return {
        "connections": [connection_json(db, c) for c in rows],
        "revoked": [connection_json(db, c) for c in revoked],
        "providers": {
            "plaid": bool(settings.plaid_client_id and settings.plaid_secret),
            "snaptrade": bool(settings.snaptrade_client_id and settings.snaptrade_consumer_key),
        },
        "people": [{"id": u.id, "name": u.name} for u in db.scalars(select(User).order_by(User.id))],
        "sync": sync_status(db),
    }


def sync_status(db: DbSession) -> dict:
    runs = db.scalars(
        select(SyncRun).where(SyncRun.finished_at.is_not(None)).order_by(SyncRun.started_at.desc()).limit(60)
    ).all()
    # Scheduled syncs in a row (newest first) that needed no fixing: the Phase 1 gate is seven nights of this.
    streak_days: set[date] = set()
    for r in runs:
        if r.trigger != "schedule":
            continue
        if not r.ok:
            break
        streak_days.add(r.started_at.date())
    last = runs[0] if runs else None
    return {
        "last_at": last.finished_at.isoformat() if last and last.finished_at else None,
        "last_ok": last.ok if last else None,
        "clean_days": len(streak_days),
        "running": db.scalar(
            select(func.count())
            .select_from(SyncRun)
            .where(SyncRun.finished_at.is_(None), SyncRun.started_at > now() - timedelta(minutes=30))
        )
        > 0,
    }


class StartLinkIn(BaseModel):
    kind: str = Field(default="banking", pattern="^(banking|investments)$")
    connection_id: int | None = None  # reconnecting this one: sign in again on the same link, nothing new is added


@router.post("/connections/{provider}/start")
def start_link(provider: str, body: StartLinkIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    settings = settings_of(request)
    db = db_of(request)
    connector = sync.connector_for(provider, settings)
    if connector is None:
        raise HTTPException(400, f"{provider.capitalize()} isn't set up on the server yet. Add its keys to .env.")
    host = request.headers.get("host", "")
    return_url = f"{request.url.scheme}://{host}/settings/accounts?linked={provider}"
    existing = None
    try:
        if provider == "snaptrade":
            # One SnapTrade user per household: a second brokerage is added to the same one.
            existing = db.scalar(
                select(Connection).where(Connection.provider == "snaptrade", Connection.token_enc.is_not(None))
            )
            token = crypto.decrypt(existing.token_enc, "connection:snaptrade") if existing else None
            start = connector.start_link(f"household-{who.user.id}", return_url, body.kind, token=token)  # type: ignore[call-arg]
        else:
            existing = db.get(Connection, body.connection_id) if body.connection_id else None
            if existing is not None and (existing.provider != provider or not existing.token_enc):
                existing = None  # revoked (or another provider's): a fresh link is the only way back
            token = crypto.decrypt(existing.token_enc, f"connection:{provider}") if existing else None
            if token:
                start = connector.start_link(f"household-{who.user.id}", return_url, body.kind, token=token)  # type: ignore[call-arg]
            else:
                start = connector.start_link(f"household-{who.user.id}", return_url, body.kind)
    except ConnectorError as e:
        raise HTTPException(502, str(e)) from None
    token = auth_challenge(db, who, provider, start.pending, existing.id if existing else None)
    return {"url": start.url, "token": token}


def auth_challenge(db: DbSession, who: Signed, provider: str, pending: dict, connection_id: int | None) -> str:
    t = auth.new_challenge(
        db,
        "link",
        who.user.id,
        provider=provider,
        connection_id=connection_id,
        pending=crypto.encrypt(json.dumps(pending), f"link:{provider}"),
    )
    c = db.scalar(select(Challenge).where(Challenge.token_hash == auth.digest(t)))
    c.expires_at = now() + timedelta(minutes=LINK_MINUTES)
    return t


class FinishLinkIn(BaseModel):
    token: str = Field(max_length=100)


@router.post("/connections/finish")
def finish_link(body: FinishLinkIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    settings = settings_of(request)
    db = db_of(request)
    c = auth.find_challenge(db, body.token, "link")
    if c is None or c.user_id != who.user.id:
        raise HTTPException(410, "This link expired. Start adding the account again.")
    provider = c.data["provider"]
    connector = sync.connector_for(provider, settings)
    if connector is None:
        raise HTTPException(400, f"{provider} isn't set up on the server.")
    pending = json.loads(crypto.decrypt(c.data["pending"], f"link:{provider}"))
    conn = db.get(Connection, c.data["connection_id"]) if c.data.get("connection_id") else None
    already = False
    linked = None
    try:
        if pending.get("update") and conn is not None:
            # Plaid update mode: same Item, same token, same accounts. Only the sign-in at the bank was renewed.
            connector.finish_update(pending)  # type: ignore[attr-defined]
        elif provider == "plaid":
            found = connector.link_result(pending)  # type: ignore[attr-defined]
            same = dedupe.matches_login(
                db, found.get("institution") or "", found.get("institution_id"), found["accounts"]
            )
            if same is not None and dedupe.healthy(same):
                # This login is already linked and working: keep that link and don't take on a second Item.
                conn, already = same, True
            else:
                linked = connector.exchange(found)  # type: ignore[attr-defined]
                # A stale link of the same login is folded into this one by the sync (dedupe.merge_duplicates).
                conn = db.scalar(
                    select(Connection).where(
                        Connection.provider == provider, Connection.external_id == linked.external_id
                    )
                )
                if found.get("institution_id"):
                    pending["institution_id"] = found["institution_id"]
        else:
            linked = connector.finish_link(pending)
    except ConnectorError as e:
        raise HTTPException(502, str(e)) from None
    db.delete(c)
    if linked is not None:
        if conn is None:
            conn = Connection(provider=provider, institution=linked.institution, created_by=who.user.id, state={})
            db.add(conn)
        conn.institution = linked.institution[:120]
        conn.external_id = linked.external_id
        conn.token_enc = crypto.encrypt(linked.token, f"connection:{provider}")
        conn.state = {**(conn.state or {}), "institution": linked.institution}
        if pending.get("institution_id"):
            conn.state["institution_id"] = pending["institution_id"]
    if conn is None:
        raise HTTPException(410, "That connection is gone. Start adding the account again.")
    if not already:
        conn.status = "active"
        conn.failure_count = 0
        conn.last_error = None
    db.flush()
    action = (
        "connection_already_linked" if already else "connection_reconnected" if linked is None else "connection_added"
    )
    audit(db, request, action, who.user.id, provider=provider, institution=conn.institution)
    db.commit()
    conn_id, institution = conn.id, conn.institution
    # First sync straight away, so the accounts show up.
    run = sync.run(request.app.state.db, settings, "manual", only=[conn_id])
    db.expire_all()
    conn = db.scalar(select(Connection).where(Connection.id == conn_id))
    if conn is None:  # folded into the older link of the same login
        conn = db.scalars(
            select(Connection)
            .where(
                Connection.provider == provider, Connection.institution == institution, Connection.status != "revoked"
            )
            .order_by(Connection.id)
        ).first()
    return {"connection": connection_json(db, conn), "synced": bool(run and run.ok), "already_linked": already}


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    hidden: bool | None = None
    is_authoritative: bool | None = None
    owner_user_id: int | None = None
    statement_cents: int | None = None
    statement_date: date | None = None
    clear_statement: bool = False


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatch, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "No such account.")
    for field in ("name", "hidden", "is_authoritative", "statement_cents", "statement_date"):
        value = getattr(body, field)
        if value is not None:
            setattr(a, field, value)
    if "owner_user_id" in body.model_fields_set:
        if body.owner_user_id is not None and db.get(User, body.owner_user_id) is None:
            raise HTTPException(422, "No such person.")
        a.owner_user_id = body.owner_user_id
    if body.statement_cents is not None and body.statement_date is None:
        a.statement_date = sync.today()
    if body.clear_statement:
        a.statement_cents = a.statement_date = None
    return connection_json(db, db.get_one(Connection, a.connection_id))


class ManualAccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    institution: str = Field(default="", max_length=120)
    type: str = Field(pattern="^(depository|credit|loan|investment|other)$")
    balance_cents: int


@router.post("/accounts/manual")
def add_manual(body: ManualAccountIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    conn = Connection(
        provider="manual", institution=body.institution.strip() or body.name.strip(), created_by=who.user.id, state={}
    )
    db.add(conn)
    db.flush()
    a = Account(
        connection_id=conn.id,
        external_id=f"manual-{conn.id}",
        name=body.name.strip(),
        institution=conn.institution,
        type=body.type,
        owner_user_id=who.user.id,
    )
    db.add(a)
    db.flush()
    sync.save_balance(db, a.id, sync.today(), body.balance_cents)
    audit(db, request, "connection_added", who.user.id, provider="manual", institution=conn.institution)
    return connection_json(db, conn)


class BalanceIn(BaseModel):
    balance_cents: int


@router.put("/accounts/{account_id}/balance")
def set_balance(account_id: int, body: BalanceIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    a = db.get(Account, account_id)
    conn = db.get(Connection, a.connection_id) if a else None
    if a is None or conn is None or conn.provider not in ("manual", "file"):
        raise HTTPException(400, "Only manual and uploaded accounts take a typed-in balance.")
    sync.save_balance(db, a.id, sync.today(), body.balance_cents)
    conn.last_synced_at = now()
    reminders.uploaded(db, [a.id])
    return connection_json(db, conn)


class ReminderIn(BaseModel):
    every: str = Field(pattern="^(monthly|quarterly|weekly|off)$")
    day: int | None = Field(default=None, ge=1, le=31)  # the statement's closing day; None: the end of the month


@router.put("/accounts/{account_id}/reminder")
def set_reminder(account_id: int, body: ReminderIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """How often to be reminded to upload a statement for an account that only gets new data by hand."""
    no_demo(who)
    db = db_of(request)
    a = db.get(Account, account_id)
    conn = db.get(Connection, a.connection_id) if a else None
    if a is None or conn is None or conn.provider not in reminders.PROVIDERS:
        raise HTTPException(400, "Only uploaded and typed-in accounts need upload reminders.")
    reminders.save_pref(db, a.id, every=body.every, day=body.day, snoozed_until=None)
    return connection_json(db, conn)


class SnoozeIn(BaseModel):
    days: int = Field(ge=1, le=7)


@router.post("/accounts/{account_id}/reminder/snooze")
def snooze_reminder(account_id: int, body: SnoozeIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    a = db.get(Account, account_id)
    conn = db.get(Connection, a.connection_id) if a else None
    if a is None or conn is None or conn.provider not in reminders.PROVIDERS:
        raise HTTPException(404, "No such account.")
    reminders.snooze(db, a.id, body.days, reminders.local_today(db))
    return {"ok": True}


def revoke_connection(settings, c: Connection) -> str | None:
    """Revokes at the provider (best effort) and forgets the token. Returns a problem to report, if any."""
    problem = None
    if c.token_enc and c.provider in ("plaid", "snaptrade"):
        connector = sync.connector_for(c.provider, settings)
        try:
            if connector is None:
                raise ConnectorError("not configured")
            connector.revoke(crypto.decrypt(c.token_enc, f"connection:{c.provider}"), c.external_id or "")
        except Exception as e:
            problem = (
                f"{c.institution}: couldn't confirm removal at {c.provider} ({getattr(e, 'code', '') or 'error'})."
            )
    c.token_enc = None
    c.status = "revoked"
    return problem


@router.delete("/connections/{connection_id}")
def remove_connection(
    connection_id: int, request: Request, delete_data: bool = False, who: Signed = Depends(signed_in)
) -> dict:
    no_demo(who)
    db = db_of(request)
    c = db.get(Connection, connection_id)
    if c is None:
        raise HTTPException(404, "No such connection.")
    problem = revoke_connection(settings_of(request), c)
    audit(db, request, "connection_removed", who.user.id, institution=c.institution, data_deleted=delete_data)
    if delete_data or c.provider in ("file", "manual"):
        db.delete(c)
    return {"ok": True, "problem": problem}


@router.post("/connections/revoke-all")
def revoke_all(request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)) -> dict:
    """The panic button: every aggregator link is removed at the provider and every token is deleted here.
    History already saved stays, so the app keeps working on what it has."""
    no_demo(who)
    settings = settings_of(request)
    db = db_of(request)
    problems = []
    count = 0
    for c in db.scalars(select(Connection).where(Connection.token_enc.is_not(None))):
        count += 1
        if p := revoke_connection(settings, c):
            problems.append(p)
    db.execute(delete(Challenge).where(Challenge.kind == "link"))
    audit(db, request, "revoke_all", who.user.id, connections=count, problems=len(problems))
    notify.send(
        settings,
        "Every connection revoked",
        f"{who.user.name} revoked every account connection. Nothing will sync until accounts are linked again.",
    )
    return {"revoked": count, "problems": problems}


@router.post("/sync")
def sync_now(request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    db.commit()
    run = sync.run(request.app.state.db, settings_of(request), "manual")
    if run is None:
        raise HTTPException(409, "A sync is already running.")
    return {"ok": run.ok, "summary": run.summary, "status": sync_status(db)}


# ---- transactions and categories


@router.get("/categories")
def categories(request: Request, who: Signed = Depends(signed_in)) -> list[dict]:
    if who.demo:
        return demo.categories()
    return [
        {"id": c.id, "name": c.name, "kind": c.kind}
        for c in db_of(request).scalars(select(Category).order_by(Category.sort, Category.name))
    ]


@router.get("/transactions")
def find_transactions(
    request: Request, f: Annotated[search.Filters, Query()], who: Signed = Depends(signed_in)
) -> dict:
    if who.demo:
        return search.search_sample(demo.household()["transactions"], demo.categories(), f)
    return search.search(db_of(request), f)


class TransactionPatch(BaseModel):
    category_id: int
    remember: bool = True  # also for every other transaction from this merchant, now and later


@router.patch("/transactions/{transaction_id}")
def recategorize(
    transaction_id: int, body: TransactionPatch, request: Request, who: Signed = Depends(signed_in)
) -> dict:
    no_demo(who)
    db = db_of(request)
    t = db.get(Transaction, transaction_id)
    category = db.get(Category, body.category_id)
    if t is None or category is None:
        raise HTTPException(404, "No such transaction or category.")
    if category.kind != "transfer":
        unmatch(db, t)
    t.category_id = category.id
    t.category_source = "user"
    changed = 0
    if body.remember and category.kind != "transfer":
        db.flush()
        changed = review.learn(db, t, category, who.user.id)
    audit(db, request, "recategorized", who.user.id, category=category.name, remember=body.remember)
    return {"id": str(t.id), "category": category.name, "category_id": category.id, "also_changed": changed}


# ---- file imports: upload -> preview -> confirm


def _account_candidates(db: DbSession) -> list[Account]:
    return list(db.scalars(select(Account).join(Connection).where(Connection.provider.in_(["file", "manual"]))))


def _builtin(data: bytes, name: str, kind: str) -> tuple[ParsedFile | None, dict | None, str]:
    """What the app reads by itself: CSV and OFX, spreadsheets laid out like a CSV it knows, and text pay stub PDFs.
    Returns (import, pay stub, why not) with at most one of the first two set."""
    if kind == "text":
        try:
            return parse(data, name, sync.today()), None, ""
        except FileError:
            return None, None, "This file's layout isn't one the app knows by itself."
    if kind == "zip":
        try:
            doc = documents.open_spreadsheet(data)
        except documents.DocumentError as e:
            raise HTTPException(422, str(e)) from None
        for sheet in doc.sheets:
            try:
                return parse(sheet.encode(), name, sync.today()), None, ""
            except FileError:
                continue
        return None, None, "This spreadsheet's layout isn't one the app knows by itself."
    if kind == "pdf":
        try:
            return None, paystub.to_json(paystub.read(data)), ""
        except paystub.StubError:
            return None, None, "The app can't read this PDF by itself."
    return None, None, "Screenshots and photos need AI to read."


@router.post("/imports")
async def upload(
    file: UploadFile, request: Request, read_with_ai: bool = False, who: Signed = Depends(signed_in)
) -> dict:
    """Reads an upload into a preview. What the app can't read by itself comes back as an offer to read it with AI
    (what would be sent, to whom); the browser uploads it again with read_with_ai=true if the person agrees."""
    no_demo(who)
    db = db_of(request)
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(422, "That file is over 5 MB. Download a shorter date range and try again.")
    name = (file.filename or "upload")[-200:]
    kind = documents.sniff(data)
    parsed, stub, why = (None, None, "") if read_with_ai else _builtin(data, name, kind)
    if stub is not None:
        return {"paystub": stub, "format": "Pay stub"}
    ai = None
    if parsed is None:
        try:
            doc = documents.open_document(data)
        except documents.DocumentError as e:
            raise HTTPException(422, str(e)) from None
        if not read_with_ai:
            return {"ai_offer": reading.offer(db, doc, reading.outgoing_for(db, doc), why)}
        try:
            r = reading.read(request.app.state.db, db, who.user.id, doc)
            audit(db, request, "ai_read_file", who.user.id, model=r.model, kind=r.answer.kind)
            if r.answer.kind == "paystub":
                return {"paystub": extract.to_paystub(r.answer, r.model), "format": "Pay stub", "ai_cost": r.cost}
            parsed = extract.to_parsed(r.answer, sync.today(), r.names, r.emails, r.model)
        except AIUnavailable as e:
            raise HTTPException(409, str(e)) from None
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None
        ai = {"model": r.model, "cost": r.cost}
    all_accounts = list(db.scalars(select(Account)))
    targets = _account_candidates(db)
    out_accounts = []
    stored_accounts = []
    for i, fa in enumerate(parsed.accounts):
        suggested = next(
            (
                a
                for a in targets
                if fa.mask and a.mask == fa.mask and (not fa.institution or a.institution == fa.institution)
            ),
            None,
        )
        # An account with these last 4 digits already syncing through Plaid or SnapTrade: don't double count.
        synced_twin = next((a for a in all_accounts if fa.mask and a.mask == fa.mask and a not in targets), None)
        duplicates = 0
        if suggested is not None and fa.transactions:
            have = set(db.scalars(select(Transaction.external_id).where(Transaction.account_id == suggested.id)))
            duplicates = sum(1 for t in fa.transactions if t.external_id in have)
        dates = sorted(t.date for t in fa.transactions)
        out_accounts.append(
            {
                "index": i,
                "institution": fa.institution,
                "name": fa.name,
                "mask": fa.mask,
                "type": fa.type,
                "holdings": len(fa.holdings),
                "holdings_cents": sum(h.value_cents for h in fa.holdings),
                "transactions": len(fa.transactions),
                "duplicates": duplicates,
                "first_date": dates[0].isoformat() if dates else None,
                "last_date": dates[-1].isoformat() if dates else None,
                "balance_cents": fa.balance_cents,
                "file_total_cents": fa.file_total_cents,
                "suggested_account_id": suggested.id if suggested else None,
                "synced_twin": household.display_name(synced_twin) if synced_twin else None,
                "spent_cents": -sum(t.amount_cents for t in fa.transactions if t.amount_cents < 0),
                "received_cents": sum(t.amount_cents for t in fa.transactions if t.amount_cents > 0),
            }
        )
        stored_accounts.append(
            {
                "institution": fa.institution,
                "name": fa.name,
                "mask": fa.mask,
                "type": fa.type,
                "balance_cents": fa.balance_cents,
                "holdings": [{**h.__dict__, "quantity": str(h.quantity), "price": str(h.price)} for h in fa.holdings],
                "transactions": [{**t.__dict__, "date": t.date.isoformat()} for t in fa.transactions],
            }
        )
    imp = Import(
        created_by=who.user.id,
        filename=name,
        kind=parsed.kind,
        parsed={"accounts": stored_accounts, "as_of": parsed.as_of.isoformat(), "flipped": parsed.flipped},
        summary={"format": parsed.format, "accounts": out_accounts, "warnings": parsed.warnings},
    )
    db.add(imp)
    db.execute(delete(Import).where(Import.status == "preview", Import.created_at < now() - timedelta(days=1)))
    db.flush()
    return {
        "id": imp.id,
        "format": parsed.format,
        "kind": parsed.kind,
        "as_of": parsed.as_of.isoformat(),
        "flipped": parsed.flipped,
        "warnings": parsed.warnings,
        "accounts": out_accounts,
        "targets": [{"id": a.id, "name": household.display_name(a), "type": a.type} for a in targets],
        "ai": ai,
    }


class ImportTarget(BaseModel):
    index: int
    account_id: int | None = None  # None: make a new account
    name: str = Field(default="", max_length=120)
    institution: str = Field(default="", max_length=120)
    type: str | None = Field(default=None, pattern="^(depository|credit|loan|investment|other)$")
    skip: bool = False


class CommitIn(BaseModel):
    targets: list[ImportTarget]
    flip: bool = False  # the person says money in and out are the wrong way round


@router.post("/imports/{import_id}/commit")
def commit_import(import_id: int, body: CommitIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    imp = db.get(Import, import_id)
    if imp is None or imp.status != "preview" or imp.created_by != who.user.id:
        raise HTTPException(404, "That upload isn't waiting to be saved any more. Upload it again.")
    allowed = {a.id: a for a in _account_candidates(db)}
    as_of = date.fromisoformat(imp.parsed["as_of"])
    totals = {"holdings": 0, "transactions": 0, "accounts": 0}
    new_connections: dict[str, Connection] = {}
    saved_into: list[int] = []
    for target in body.targets:
        if target.skip or not 0 <= target.index < len(imp.parsed["accounts"]):
            continue
        fa = imp.parsed["accounts"][target.index]
        if target.account_id is not None:
            account = allowed.get(target.account_id)
            if account is None:
                raise HTTPException(422, "Files can only go into uploaded or manual accounts, or a new one.")
            conn = db.get(Connection, account.connection_id)
        else:
            institution = (target.institution or fa["institution"] or "Uploaded").strip()[:120]
            name = (target.name or fa["name"] or institution).strip()[:120]
            # Accounts from one file at one institution (Fidelity's file has them all) share one connection.
            conn = new_connections.get(institution)
            if conn is None:
                conn = Connection(provider="file", institution=institution, created_by=who.user.id, state={})
                db.add(conn)
                db.flush()
                new_connections[institution] = conn
            account = Account(
                connection_id=conn.id,
                external_id=fa["mask"] or f"file-{conn.id}",
                name=name,
                institution=institution,
                type=target.type or fa["type"],
                mask=fa["mask"],
                owner_user_id=who.user.id,
            )
            db.add(account)
            db.flush()
        if conn is None:
            raise HTTPException(404, "No such account.")
        mapping = {"file": account, (fa["mask"] or "file"): account}
        for h in fa["holdings"]:
            mapping[h["account_external_id"]] = account
        if fa["holdings"]:
            holdings = [
                HoldingData(**{**h, "quantity": Decimal(h["quantity"]), "price": Decimal(h["price"])})
                for h in fa["holdings"]
            ]
            totals["holdings"] += sync.save_holdings(db, mapping, holdings, as_of)
        if fa["transactions"]:
            txns = []
            for t in fa["transactions"]:
                data = TransactionData(**{**t, "date": date.fromisoformat(t["date"])})
                if body.flip:
                    data.amount_cents = -data.amount_cents
                data.account_external_id = "file"
                txns.append(data)
            totals["transactions"] += len(sync.save_transactions(db, {"file": account}, txns, "file", imp.id))
        if fa["balance_cents"] is not None:
            sync.save_balance(db, account.id, as_of, fa["balance_cents"])
        conn.last_synced_at = now()
        saved_into.append(account.id)
        totals["accounts"] += 1
    imp.status = "committed"
    reminders.uploaded(db, saved_into)
    db.flush()
    match_transfers(db, as_of - timedelta(days=400))
    categorize(db)
    audit(db, request, "import", who.user.id, format=imp.summary.get("format"), **totals)
    return totals


@router.delete("/imports/{import_id}")
def discard_import(import_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    imp = db.get(Import, import_id)
    if imp is not None and imp.status == "preview" and imp.created_by == who.user.id:
        imp.status = "discarded"
        imp.parsed = {}
    return {"ok": True}
