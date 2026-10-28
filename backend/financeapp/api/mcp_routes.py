"""The read-only MCP server at /mcp, and Settings, Agents, where the owner makes and revokes the keys it takes.

Any MCP-capable agent (Claude Code, Claude Desktop, Cursor, your own scripts) can ask questions of the household's
numbers through the tools in agents.py. It is MCP's Streamable HTTP transport in its simplest form: one JSON-RPC
request per POST, one JSON answer, no sessions or event streams, and no tool that changes anything.

A key is a bearer token: only its SHA-256 is stored, it is shown once, and revoking it stops it at once. Browser
cookies are never looked at here, a request a browser sends from another site is refused, and each key is limited to
two requests a second on average, so a runaway agent can't hammer the database.
"""

import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool

from financeapp import agents, alerts, auth, cards, nearby, offers
from financeapp.api.deps import Signed, audit, client_ip, owner, recent, signed_in
from financeapp.models import AgentKey, User, now
from financeapp.sync import today

router = APIRouter()
KEY_PREFIX = "fa_agent_"
MAX_KEYS = 20
MAX_BODY = 64 * 1024
PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = (
    "Read-only access to one household's finances: accounts, balances, transactions, spending by category, the "
    "budget, investments, net worth history, alerts and the morning brief. Amounts are US dollars; negative "
    "transaction amounts are money out. Call overview first for the big picture."
)


# ---- Settings, Agents


def db_of(request: Request) -> DbSession:
    return request.state.db


def key_json(k: AgentKey, people: dict[int, str]) -> dict:
    return {
        "id": k.id,
        "label": k.label,
        "hint": k.hint,
        "by": people.get(k.created_by) if k.created_by else None,
        "created_at": k.created_at.isoformat(),
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "uses": k.uses,
    }


@router.get("/api/agents")
def list_keys(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return {"keys": [], "can_edit": False, "tools": [t.title for t in agents.TOOLS.values()]}
    db = db_of(request)
    people = {u.id: u.name for u in db.scalars(select(User))}
    keys = db.scalars(select(AgentKey).where(AgentKey.revoked_at.is_(None)).order_by(AgentKey.id)).all()
    return {
        "keys": [key_json(k, people) for k in keys],
        "can_edit": who.user.role == "owner",
        "tools": [t.title for t in agents.TOOLS.values()],
    }


class KeyIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.post("/api/agents")
def create_key(body: KeyIn, request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)) -> dict:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    label = body.label.strip()
    if not label:
        raise HTTPException(422, "Give the key a name, like the agent that will use it.")
    if len(db.scalars(select(AgentKey.id).where(AgentKey.revoked_at.is_(None))).all()) >= MAX_KEYS:
        raise HTTPException(422, f"There are already {MAX_KEYS} keys. Revoke one you don't use first.")
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    k = AgentKey(label=label, token_hash=auth.digest(token), hint=token[-4:], created_by=who.user.id, uses=0)
    db.add(k)
    db.flush()
    audit(db, request, "agent_key_added", who.user.id, label=label)
    # The only time the key leaves the server.
    return {"key": key_json(k, {who.user.id: who.user.name}), "token": token}


@router.delete("/api/agents/{key_id}")
def revoke_key(key_id: int, request: Request, who: Signed = Depends(owner)) -> dict:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    k = db.get(AgentKey, key_id)
    if k is None or k.revoked_at is not None:
        raise HTTPException(404, "That key is already gone.")
    k.revoked_at = now()
    audit(db, request, "agent_key_revoked", who.user.id, label=k.label)
    return {"ok": True}


# ---- the MCP endpoint


class BadParams(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def rpc_error(id_: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}, status_code=status)


def find_key(db: DbSession, header: str) -> AgentKey | None:
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX) or len(token) > 100:
        return None
    return db.scalar(select(AgentKey).where(AgentKey.token_hash == auth.digest(token), AgentKey.revoked_at.is_(None)))


def answer(method: str, params: dict, db: DbSession, version: str) -> dict:
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "financeapp", "title": "Finance", "version": version},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [t.listing() for t in agents.TOOLS.values()]}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or not (arguments is None or isinstance(arguments, dict)):
            raise BadParams("tools/call needs a name and an arguments object.")
        try:
            result = agents.call(db, name, arguments)
        except agents.ToolError as e:
            return {"content": [{"type": "text", "text": e.message}], "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(result)}], "structuredContent": result}
    raise LookupError(method)


def handle(request: Request, msg: Any) -> Response:
    """The part that touches the database, run in a worker thread."""
    app = request.app
    ip = client_ip(request)
    with app.state.db.session() as db:
        key = find_key(db, request.headers.get("authorization", ""))
        if key is None:
            app.state.agent_failures.allow(ip)
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": NEEDS_KEY}},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="financeapp"'},
            )
        if not app.state.agent_limiter.allow(str(key.id)):
            return rpc_error(None, -32004, "Too many requests. Slow down a little.", 429)

        if isinstance(msg, list):
            return rpc_error(None, -32600, "Send one request at a time.", 400)
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return rpc_error(None, -32600, "That isn't a JSON-RPC 2.0 request.", 400)
        if "method" not in msg:
            return Response(status_code=202)  # a reply to something we never ask: nothing to do
        method = msg["method"]
        if "id" not in msg:
            return Response(status_code=202)  # a notification, e.g. notifications/initialized
        id_ = msg["id"]
        params = msg.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return rpc_error(id_, -32600, "method must be a string and params an object.")

        key.last_used_at = now()
        key.uses += 1
        try:
            result = answer(method, params, db, app.state.settings.app_version)
        except LookupError:
            return rpc_error(id_, -32601, "There's no method by that name.")
        except BadParams as e:
            return rpc_error(id_, -32602, e.message)
        return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


NEEDS_KEY = "This needs an agent key from Settings, Agents, sent as 'Authorization: Bearer <key>'."


@router.api_route("/mcp", methods=["GET", "DELETE", "PUT", "PATCH"], include_in_schema=False)
def mcp_other() -> Response:
    # No server-to-client stream and no sessions to end.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp", include_in_schema=False)
async def mcp(request: Request) -> Response:
    app = request.app
    if app.state.settings.demo_mode or app.state.db is None:
        return rpc_error(None, -32002, "There's no agent access with sample data.", 404)
    # A web page in a browser must not be able to talk to this (MCP's DNS-rebinding advice); agents send no Origin.
    origin = request.headers.get("origin")
    if origin is not None and origin.split("://", 1)[-1] != request.headers.get("host", ""):
        return rpc_error(None, -32003, "Cross-site request refused.", 403)
    if app.state.agent_failures.exceeded(client_ip(request)):
        return rpc_error(None, -32004, "Too many tries with a wrong key. Wait a few minutes.", 429)
    body = await request.body()
    if len(body) > MAX_BODY:
        return rpc_error(None, -32600, "That request is too big.", 413)
    try:
        msg = json.loads(body)
    except ValueError:
        return rpc_error(None, -32700, "That isn't JSON.", 400)
    return await run_in_threadpool(handle, request, msg)


# ---- the "offers here" hook, for an iPhone Shortcuts automation (nearby.py)


@router.post("/hook/offers-here", include_in_schema=False)
async def offers_here(request: Request) -> Response:
    """Sent by a Shortcuts automation as you arrive somewhere: {"lat": .., "lon": ..} with an agent key. Pushes the
    card offers at stores around you (once a day each) and says how many. The spot is rounded and not kept."""
    app = request.app
    if app.state.settings.demo_mode or app.state.db is None:
        return JSONResponse({"detail": "Not available with sample data."}, status_code=404)
    origin = request.headers.get("origin")
    if origin is not None and origin.split("://", 1)[-1] != request.headers.get("host", ""):
        return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    if app.state.agent_failures.exceeded(client_ip(request)):
        return JSONResponse({"detail": "Too many tries with a wrong key. Wait a few minutes."}, status_code=429)
    body = await request.body()
    try:
        spot = json.loads(body) if len(body) <= 1_000 else None
        lat, lon = float(spot["lat"]), float(spot["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"detail": 'Send {"lat": number, "lon": number}.'}, status_code=422)
    return await run_in_threadpool(_offers_here, request, lat, lon)


def _offers_here(request: Request, lat: float, lon: float) -> Response:
    app = request.app
    with app.state.db.session() as db:
        key = find_key(db, request.headers.get("authorization", ""))
        if key is None:
            app.state.agent_failures.allow(client_ip(request))
            return JSONResponse({"detail": NEEDS_KEY}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        if not app.state.agent_limiter.allow(str(key.id)):
            return JSONResponse({"detail": "Too many requests. Slow down a little."}, status_code=429)
        key.last_used_at = now()
        key.uses += 1
        on = today()
        saved = offers.load(db, on)
        if not saved:
            return JSONResponse({"found": 0, "pushed": 0})
        try:
            around = nearby.places(lat, lon)
        except nearby.Unavailable as e:
            return JSONResponse({"detail": str(e)}, status_code=502)
        _, accounts, links = cards.load_cards(db)
        labels = {a.id: cards.label_of(a, links.get(a.id)) for a in accounts}
        found = [
            nearby.describe(o, p, labels[o.account_id], on)
            for o, p in nearby.matches(saved, around)
            if o.account_id in labels
        ]
        pushed = nearby.queue(db, found, on)
        if pushed:
            alerts.send_pending(db, app.state.settings)
        return JSONResponse({"found": len(found), "pushed": pushed})
