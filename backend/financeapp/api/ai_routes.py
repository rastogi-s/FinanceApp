"""Settings, AI: providers and their keys, which model does which task, the monthly cap, and the usage log.

Everyone signed in can see what's set up and what it has cost; only the owner can change it, and adding or replacing
a key (or removing a provider) needs a fresh "it's you" check. Keys are encrypted at rest and never sent back: the
page only ever sees their last 4 characters.
"""

from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import crypto
from financeapp.ai import providers, service
from financeapp.ai.providers import ProviderError
from financeapp.ai.service import TASKS, AIUnavailable
from financeapp.api.deps import Signed, audit, owner, recent, signed_in
from financeapp.models import AIProvider, AIUsage, User, now

router = APIRouter(prefix="/api/ai")
KEY_PURPOSE = "ai_providers.api_key"
KEY_URLS = {
    "anthropic": "https://platform.claude.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
}


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


def dollars(micro: int) -> float:
    return round(micro / 1_000_000, 4)


def provider_json(p: AIProvider) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "kind_name": providers.NAMES.get(p.kind, p.kind),
        "label": p.label,
        "base_url": p.base_url,
        "key_hint": p.key_hint,
        "enabled": p.enabled,
        "allow_pictures": p.allow_pictures,
        "models": p.models.get("ids", []),
        "last_checked_at": p.last_checked_at.isoformat() if p.last_checked_at else None,
        "last_error": p.last_error,
    }


def overview(db: DbSession, who: Signed) -> dict:
    s = service.settings(db)
    rows = list(db.scalars(select(AIProvider).order_by(AIProvider.created_at)))
    tasks = []
    for task_id, t in TASKS.items():
        route = s["routes"].get(task_id)
        try:
            r = service.route_for(db, task_id, pictures=False)
            price, problem = {"in": r.price[0], "out": r.price[1], "known": r.price[2]}, None
            if t.pictures and not r.provider.allow_pictures:
                problem = "Text only: pictures and scans won't be sent."
        except AIUnavailable as e:
            price, problem = None, str(e)
        tasks.append(
            {
                "id": task_id,
                "title": t.title,
                "detail": t.detail,
                "pictures": t.pictures,
                "route": route,
                "price": price,
                "ready": price is not None,
                "problem": problem,
            }
        )
    usage = db.execute(
        select(AIUsage, User.name)
        .outerjoin(User, User.id == AIUsage.user_id)
        .where(AIUsage.at >= now() - timedelta(days=90))
        .order_by(AIUsage.at.desc())
        .limit(40)
    ).all()
    month = service.month_start()
    return {
        "providers": [provider_json(p) for p in rows],
        "tasks": tasks,
        "cap_cents": s["monthly_cap_cents"],
        "spent": dollars(service.spent_this_month(db)),
        "guessed": db.scalar(select(AIUsage.id).where(AIUsage.at >= month, AIUsage.priced.is_(False)).limit(1))
        is not None,
        "usage": [
            {
                "id": u.id,
                "at": u.at.isoformat(),
                "who": name,
                "task": TASKS[u.task].title if u.task in TASKS else u.task,
                "model": u.model,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cost": dollars(u.cost_microdollars),
                "priced": u.priced,
                "ok": u.ok,
                "error": u.error,
            }
            for u, name in usage
        ],
        "kinds": [
            {
                "kind": k,
                "name": providers.NAMES[k],
                "needs_key": k != "openai_compatible",
                "suggested_model": service.SUGGESTED.get(k),
                "key_url": KEY_URLS.get(k),
            }
            for k in providers.KINDS
        ],
        "can_edit": who.user.role == "owner" and not who.demo,
        "encryption": crypto.key_configured(),
    }


@router.get("")
def get_ai(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return {
            "providers": [],
            "tasks": [
                {"id": k, "title": t.title, "detail": t.detail, "pictures": t.pictures, "route": None}
                | {"price": None, "ready": False, "problem": "Not available with sample data."}
                for k, t in TASKS.items()
            ],
            "cap_cents": service.DEFAULT_CAP_CENTS,
            "spent": 0,
            "guessed": False,
            "usage": [],
            "kinds": [],
            "can_edit": False,
            "encryption": True,
        }
    return overview(db_of(request), who)


def check(p: AIProvider) -> None:
    """Lists the provider's models with its key: proves the key and address work, and costs nothing."""
    try:
        ids = providers.list_models(service.endpoint(p))
    except ProviderError as e:
        p.last_error = str(e)[:300]
        raise
    p.models = {"ids": ids}
    p.last_checked_at = now()
    p.last_error = None


def hint(key: str) -> str:
    return key[-4:] if len(key) >= 12 else ""


class ProviderIn(BaseModel):
    kind: str = Field(pattern="^(anthropic|openai|gemini|openai_compatible)$")
    label: str = Field(default="", max_length=80)
    api_key: str = Field(default="", max_length=400)
    base_url: str = Field(default="", max_length=300)


@router.post("/providers")
def add_provider(body: ProviderIn, request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)) -> dict:
    no_demo(who)
    db = db_of(request)
    key = body.api_key.strip()
    if body.kind != "openai_compatible" and not key:
        raise HTTPException(422, "Paste the API key.")
    if any(c.isspace() for c in key):
        raise HTTPException(422, "That key has spaces in it. Copy it again.")
    try:
        base_url = providers.check_base_url(body.base_url) if body.kind == "openai_compatible" else None
    except ProviderError as e:
        raise HTTPException(422, str(e)) from None
    p = AIProvider(
        kind=body.kind,
        label=(body.label.strip() or providers.NAMES[body.kind])[:80],
        base_url=base_url,
        api_key_enc=crypto.encrypt(key, KEY_PURPOSE) if key else None,
        key_hint=hint(key) or None,
        enabled=True,
        # Pictures can't be redacted, so they only go to your own machine unless you say otherwise.
        allow_pictures=base_url is not None and providers.is_local_host(urlsplit(base_url).hostname or ""),
        models={},
        created_by=who.user.id,
    )
    try:
        check(p)
    except ProviderError as e:
        raise HTTPException(422, str(e)) from None
    db.add(p)
    db.flush()
    # The first provider picks up every task that has nowhere to go yet, with the suggested (or first) model.
    s = service.settings(db)
    routes = dict(s["routes"])
    model = service.SUGGESTED.get(p.kind)
    if model not in p.models["ids"]:
        model = p.models["ids"][0] if p.models["ids"] else None
    if model:
        for task_id in TASKS:
            if not routes.get(task_id) or db.get(AIProvider, routes[task_id].get("provider_id") or 0) is None:
                routes[task_id] = {"provider_id": p.id, "model": model}
        service.save_settings(db, routes=routes)
    audit(db, request, "ai_provider_added", who.user.id, kind=p.kind)
    return overview(db, who)


class ProviderPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    allow_pictures: bool | None = None


@router.patch("/providers/{provider_id}")
def patch_provider(provider_id: int, body: ProviderPatch, request: Request, who: Signed = Depends(owner)) -> dict:
    no_demo(who)
    db = db_of(request)
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(404, "No such provider.")
    changes = body.model_dump(exclude_none=True)
    if "label" in changes:
        p.label = changes["label"].strip()[:80] or providers.NAMES[p.kind]
    if "enabled" in changes:
        p.enabled = changes["enabled"]
    if "allow_pictures" in changes:
        p.allow_pictures = changes["allow_pictures"]
    audit(db, request, "ai_provider_changed", who.user.id, provider=p.label, **changes)
    return overview(db, who)


class KeyIn(BaseModel):
    api_key: str = Field(default="", max_length=400)
    base_url: str = Field(default="", max_length=300)


@router.put("/providers/{provider_id}/key")
def replace_key(
    provider_id: int, body: KeyIn, request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)
) -> dict:
    """A new key (or server address). Checked before it's kept, so a typo never replaces a working key."""
    no_demo(who)
    db = db_of(request)
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(404, "No such provider.")
    key = body.api_key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(422, "That key has spaces in it. Copy it again.")
    old = (p.api_key_enc, p.key_hint, p.base_url)
    try:
        if p.kind == "openai_compatible":
            p.base_url = providers.check_base_url(body.base_url or p.base_url or "")
            if key or body.base_url:
                p.api_key_enc = crypto.encrypt(key, KEY_PURPOSE) if key else None
                p.key_hint = hint(key) or None
        elif key:
            p.api_key_enc, p.key_hint = crypto.encrypt(key, KEY_PURPOSE), hint(key) or None
        else:
            raise HTTPException(422, "Paste the new key.")
        check(p)
    except ProviderError as e:
        p.api_key_enc, p.key_hint, p.base_url = old
        raise HTTPException(422, str(e)) from None
    audit(db, request, "ai_key_changed", who.user.id, provider=p.label)
    return overview(db, who)


@router.post("/providers/{provider_id}/check")
def check_provider(provider_id: int, request: Request, who: Signed = Depends(owner)) -> dict:
    no_demo(who)
    db = db_of(request)
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(404, "No such provider.")
    try:
        check(p)
    except ProviderError as e:
        # Keep the error on the provider (the route's transaction commits) and say what went wrong.
        return overview(db, who) | {"error": str(e)}
    return overview(db, who)


@router.delete("/providers/{provider_id}")
def remove_provider(
    provider_id: int, request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)
) -> dict:
    no_demo(who)
    db = db_of(request)
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(404, "No such provider.")
    routes = {k: v for k, v in service.settings(db)["routes"].items() if v and v.get("provider_id") != p.id}
    service.save_settings(db, routes=routes)
    audit(db, request, "ai_provider_removed", who.user.id, provider=p.label)
    db.delete(p)
    db.flush()
    return overview(db, who)


class RouteIn(BaseModel):
    provider_id: int
    model: str = Field(min_length=1, max_length=120)
    price_in: float | None = Field(default=None, ge=0, le=1000)
    price_out: float | None = Field(default=None, ge=0, le=1000)


class SettingsIn(BaseModel):
    monthly_cap_cents: int | None = Field(default=None, ge=0, le=service.MAX_CAP_CENTS)
    routes: dict[str, RouteIn | None] | None = None


@router.put("/settings")
def save_settings(body: SettingsIn, request: Request, who: Signed = Depends(owner)) -> dict:
    no_demo(who)
    db = db_of(request)
    changes: dict = {}
    if body.monthly_cap_cents is not None:
        changes["monthly_cap_cents"] = body.monthly_cap_cents
    if body.routes is not None:
        routes = dict(service.settings(db)["routes"])
        for task_id, r in body.routes.items():
            if task_id not in TASKS:
                raise HTTPException(422, "Unknown AI task.")
            if r is None:
                routes.pop(task_id, None)
                continue
            if db.get(AIProvider, r.provider_id) is None:
                raise HTTPException(422, "No such provider.")
            try:
                providers.check_model(r.model.strip())
            except ProviderError as e:
                raise HTTPException(422, str(e)) from None
            routes[task_id] = r.model_dump(exclude_none=True) | {"model": r.model.strip()}
        changes["routes"] = routes
    service.save_settings(db, **changes)
    audit(db, request, "ai_settings_changed", who.user.id, cap_cents=body.monthly_cap_cents)
    return overview(db, who)


class TryIn(BaseModel):
    task: str


@router.post("/try")
def try_route(body: TryIn, request: Request, who: Signed = Depends(owner)) -> dict:
    """A tiny request through a task's route (a few tokens), to show the model answers. Logged like any other."""
    no_demo(who)
    db = db_of(request)
    req = providers.Request(
        system="You check that an integration works.",
        text='Reply with this JSON and nothing else: {"ready": true}',
        max_tokens=200,
    )
    try:
        reply, route = service.run(request.app.state.db, db, body.task, who.user.id, req)
        ok = providers.parse_json(reply.text).get("ready") is True
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, str(e)) from None
    return overview(db, who) | {
        "tried": {
            "ok": ok,
            "model": route.model,
            "tokens": reply.input_tokens + reply.output_tokens,
            "cost": dollars(service.microdollars(reply.input_tokens, reply.output_tokens, route.price)),
        }
    }
