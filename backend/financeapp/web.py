"""Serves the built Next.js app (a static export) and sets the security headers on every response.

Files are indexed once at startup, so a request can only ever return a file that was in the build: there is no
path joining on request input and nothing to traverse. Each HTML page gets its own Content-Security-Policy with
the SHA-256 of every inline script Next.js wrote into it, so the browser runs those scripts and nothing else.
"""

import base64
import hashlib
import mimetypes
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _InlineScripts(HTMLParser):
    """Collects the exact text of every inline <script> (one without src) in a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and not any(name == "src" for name, _ in attrs):
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def inline_script_hashes(html: bytes) -> list[str]:
    parser = _InlineScripts()
    parser.feed(html.decode("utf-8"))
    parser.close()
    return [
        base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
        for script in parser.scripts
        if script.strip()
    ]


# Next.js content-hashes everything under _next/static, so those files never change at a given URL.
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def page_csp(script_hashes: list[str], upgrade: bool) -> str:
    scripts = " ".join(["'self'", *(f"'sha256-{h}'" for h in script_hashes)])
    directives = [
        "default-src 'none'",
        f"script-src {scripts}",
        # Inline style attributes are needed by Radix UI positioning; styles cannot run code.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ]
    if upgrade:
        directives.append("upgrade-insecure-requests")
    return "; ".join(directives)


@dataclass(frozen=True)
class StaticFile:
    path: Path
    media_type: str
    cache_control: str
    body: bytes | None = None  # HTML pages are kept in memory
    csp: str | None = None


class StaticSite:
    def __init__(self, root: Path, hsts: bool) -> None:
        self.root = root
        self.files: dict[str, StaticFile] = {}
        self.not_found: StaticFile | None = None
        if root.is_dir():
            self._index(hsts)

    @property
    def available(self) -> bool:
        return "/" in self.files

    def _index(self, hsts: bool) -> None:
        for file in sorted(p for p in self.root.rglob("*") if p.is_file()):
            rel = file.relative_to(self.root).as_posix()
            if file.suffix == ".html":
                body = file.read_bytes()
                hashes = inline_script_hashes(body)
                entry = StaticFile(file, "text/html; charset=utf-8", REVALIDATE, body, page_csp(hashes, hsts))
                if rel == "404.html":
                    self.not_found = entry
                for url in self._page_urls(rel):
                    self.files.setdefault(url, entry)
            else:
                media_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                if file.suffix == ".webmanifest":
                    media_type = "application/manifest+json"
                cache = IMMUTABLE if rel.startswith("_next/static/") else REVALIDATE
                self.files["/" + rel] = StaticFile(file, media_type, cache)

    @staticmethod
    def _page_urls(rel: str) -> list[str]:
        stem = rel.removesuffix(".html")
        if stem == "index":
            return ["/"]
        if stem.endswith("/index"):
            base = "/" + stem.removesuffix("/index")
            return [base, base + "/"]
        return ["/" + stem, "/" + stem + "/", "/" + rel]

    def response(self, request: Request) -> Response:
        entry = self.files.get(request.url.path)
        status = 200
        if entry is None:
            entry, status = self.not_found, 404
            if entry is None:
                return Response("Not found", status_code=404, media_type="text/plain")
        headers = {"Cache-Control": entry.cache_control}
        if entry.csp:
            headers["Content-Security-Policy"] = entry.csp
        if entry.body is not None:
            return Response(entry.body, status_code=status, media_type=entry.media_type, headers=headers)
        if entry.path.name == "sw.js":
            headers["Service-Worker-Allowed"] = "/"
        return FileResponse(entry.path, media_type=entry.media_type, headers=headers)


class SecurityHeaders:
    """Adds the headers every response carries. Pages set their own CSP; anything else gets a deny-all one."""

    def __init__(self, app: ASGIApp, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith("/api/") or scope["path"] == "/health"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", API_CSP)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                # Location only for this app's own pages (Credit cards, Offers near me), asked each time.
                headers["Permissions-Policy"] = (
                    "accelerometer=(), camera=(), geolocation=(self), gyroscope=(), magnetometer=(), "
                    "microphone=(), payment=(), usb=(), interest-cohort=()"
                )
                if self.hsts:
                    headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
                if is_api:
                    # Financial data is never stored by the browser or anything in between.
                    headers["Cache-Control"] = "no-store"
                if "server" in headers:
                    del headers["server"]
            await send(message)

        await self.app(scope, receive, send_with_headers)
