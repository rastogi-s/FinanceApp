"""The FastAPI app: a JSON API under /api, /health for the updater, and the built web app for everything else."""

import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from financeapp import auth, crypto
from financeapp.api import ai_routes, alert_routes, auth_routes, budget_routes, money_routes, portfolio_routes
from financeapp.config import PACKAGE_DIR, Settings, get_settings
from financeapp.db import Database
from financeapp.scheduler import Scheduler
from financeapp.web import SecurityHeaders, StaticSite

log = logging.getLogger("financeapp")


@lru_cache
def whats_new() -> list[dict]:
    return json.loads((PACKAGE_DIR / "data" / "whats-new.json").read_text())


class SameOrigin:
    """Refuses state-changing API requests that a browser says came from another site. With SameSite=Strict
    cookies and the per-session CSRF token (deps.signed_in) this is the third lock on the same door."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and scope["path"].startswith("/api/")
        ):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            site = headers.get("sec-fetch-site")
            origin = headers.get("origin")
            host = headers.get("host", "")
            cross = (site is not None and site not in ("same-origin", "none")) or (
                origin is not None and origin.split("://", 1)[-1] != host
            )
            if cross:
                await JSONResponse({"detail": "Cross-site request refused."}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BodyLimit:
    """Caps request bodies (uploads are the biggest: 5 MB) before anything reads them."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            await JSONResponse({"detail": "That's too big to upload."}, status_code=413)(scope, receive, send)
            return
        seen = 0

        async def limited() -> dict:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise ValueError("request body too large")
            return message

        await self.app(scope, limited, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    crypto.configure(settings.encryption_key)
    database = Database(settings.database_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if database is not None and not settings.demo_mode:
            database.migrate()
            with database.session() as db:
                code = auth.issue_setup_code(db)
            if code:
                log.warning(
                    "\n\n    First-run setup code:  %s   (valid 24 hours)\n"
                    "    Open the app and enter it to create the first account.\n",
                    code,
                )
            if settings.sync_enabled:
                scheduler = Scheduler(database, settings)
                scheduler.start()
        yield
        if scheduler:
            scheduler.shutdown()

    # No interactive docs or schema: nothing about the API is published.
    app = FastAPI(title="Finance App", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = database
    # Sign-in attempts per IP address: 20 in 5 minutes (on top of the per-account lockout).
    app.state.limiter = auth.RateLimiter(limit=20, window_seconds=300)
    site = StaticSite(settings.static_dir, hsts=settings.hsts)

    @app.get("/health")
    def health() -> JSONResponse:
        # Demo mode runs without a database; everything else needs one, and the encryption key.
        if database is None:
            db, ok = "not configured", settings.demo_mode
        else:
            ok = database.ok()
            db = "ok" if ok else "unreachable"
        key = crypto.key_configured()
        if not settings.demo_mode and not key:
            ok = False
        body = {"status": "ok" if ok else "degraded", "version": settings.app_version, "database": db}
        if not settings.demo_mode:
            body["encryption_key"] = "ok" if key else "missing"
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/api/meta")
    def meta() -> dict:
        return {"version": settings.app_version, "demo": settings.demo_mode}

    @app.get("/api/whats-new")
    def get_whats_new() -> list[dict]:
        return whats_new()

    app.include_router(auth_routes.router)
    app.include_router(money_routes.router)
    app.include_router(budget_routes.router)
    app.include_router(ai_routes.router)
    app.include_router(alert_routes.router)
    app.include_router(portfolio_routes.router)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.api_route("/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(request: Request) -> Response:
        return site.response(request)

    app.add_middleware(BodyLimit, max_bytes=6 * 1024 * 1024)
    app.add_middleware(SameOrigin)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(SecurityHeaders, hsts=settings.hsts)
    return app


logging.getLogger("financeapp").setLevel(logging.INFO)
if not logging.getLogger("financeapp").handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("financeapp").addHandler(_h)

app = create_app()
