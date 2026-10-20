"""The FastAPI app: a small JSON API under /api, /health for the updater, and the built web app for everything else."""

import json
from functools import lru_cache

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from financeapp import demo
from financeapp.config import PACKAGE_DIR, Settings, get_settings
from financeapp.db import database_ok
from financeapp.web import SecurityHeaders, StaticSite


@lru_cache
def whats_new() -> list[dict]:
    return json.loads((PACKAGE_DIR / "data" / "whats-new.json").read_text())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # No interactive docs or schema: nothing about the API is published.
    app = FastAPI(title="Finance App", docs_url=None, redoc_url=None, openapi_url=None)
    site = StaticSite(settings.static_dir, hsts=settings.hsts)

    @app.get("/health")
    def health() -> JSONResponse:
        # Demo mode runs without a database; everything else needs one.
        if not settings.database_url:
            db, ok = "not configured", settings.demo_mode
        else:
            ok = database_ok()
            db = "ok" if ok else "unreachable"
        body = {"status": "ok" if ok else "degraded", "version": settings.app_version, "database": db}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/api/meta")
    def meta() -> dict:
        return {"version": settings.app_version, "demo": settings.demo_mode}

    @app.get("/api/whats-new")
    def get_whats_new() -> list[dict]:
        return whats_new()

    @app.get("/api/household")
    def household() -> dict:
        return demo.household() if settings.demo_mode else demo.empty()

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.api_route("/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(request: Request) -> Response:
        return site.response(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(SecurityHeaders, hsts=settings.hsts)
    return app


app = create_app()
