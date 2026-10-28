"""Talking to each AI provider over HTTPS: list its models (free, used to check a key) and send one request.

Every provider gets the same request (a system prompt, text, optional pictures, an optional JSON schema) and gives
back the same reply (text and token counts), so the rest of the app never knows which one answered. Calls never
follow redirects (a key must only ever go to the host it was set up for) and errors never carry the key.
"""

import base64
import ipaddress
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

KINDS = ("anthropic", "openai", "gemini", "openai_compatible")
NAMES = {
    "anthropic": "Claude",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "openai_compatible": "Local or other",
}
ANTHROPIC_URL = "https://api.anthropic.com/v1"
OPENAI_URL = "https://api.openai.com/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")
# Models in OpenAI's list that can't answer a chat request.
NOT_CHAT = ("embedding", "tts", "whisper", "dall-e", "moderation", "audio", "realtime", "transcribe", "image", "search")
TIMEOUT = httpx.Timeout(120, connect=10)


class ProviderError(RuntimeError):
    """A call failed. The message is shown to the person as-is, so it never includes the key. `reply` is set when
    the provider answered (and billed) but the answer can't be used, so the usage log still counts it."""

    def __init__(self, message: str, reply: "Reply | None" = None, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.reply = reply
        self.status = status  # the provider's HTTP status, when it answered with an error


@dataclass
class Picture:
    mime: str  # image/png | image/jpeg | image/webp
    data: bytes


@dataclass
class Request:
    system: str
    text: str
    pictures: list[Picture] = field(default_factory=list)
    schema: dict | None = None  # ask for JSON matching this (where the provider can enforce it)
    max_tokens: int = 8000
    # Let the model search the web itself, up to this many times (Claude only: Anthropic runs the searches, so
    # nothing here fetches outside pages). A schema can't be enforced alongside it; the prompt asks for JSON instead.
    web_searches: int = 0
    timeout: float | None = None  # seconds; searching takes longer than a plain answer


@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    web_searches: int = 0  # searches run (and billed) for this answer
    # Pages the searches turned up and the ones the answer cites: {"url", "title", "age" or "quote"}. Outside text.
    found: list[dict] = field(default_factory=list)
    cited: list[dict] = field(default_factory=list)


@dataclass
class Endpoint:
    kind: str
    api_key: str | None
    base_url: str | None = None


def is_local_host(host: str) -> bool:
    """A host on this machine or the home network, where plain http is acceptable (Ollama on the same box)."""
    host = host.lower().strip("[]")
    if host in ("localhost", "host.docker.internal") or host.endswith((".local", ".lan", ".internal", ".ts.net")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")  # Tailscale


def check_base_url(url: str) -> str:
    """The server address for an OpenAI-compatible provider: https anywhere, http only on a local host."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProviderError("Enter the server's address, like http://localhost:11434/v1.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ProviderError("Leave names, passwords and ?options out of the address.")
    if parts.scheme == "http" and not is_local_host(parts.hostname):
        raise ProviderError("Use https for a server that isn't on this machine or your home network.")
    return url


def check_model(model: str) -> str:
    if not MODEL_RE.match(model):
        raise ProviderError("That model name doesn't look right.")
    return model


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _fail(r: httpx.Response, who: str) -> ProviderError:
    message = ""
    try:
        body = r.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or "")
        elif isinstance(err, str):
            message = err
    except ValueError:
        pass
    message = message[:160]
    code = r.status_code
    if code in (401, 403):
        return ProviderError(f"{_cap(who)} didn't accept the key. Check it and try again.", status=code)
    if code == 429:
        return ProviderError(f"{_cap(who)} says slow down or add credit ({message or 'rate limited'}).", status=code)
    if code == 404:
        return ProviderError(f"{_cap(who)} doesn't know that model{f' ({message})' if message else ''}.", status=code)
    if 300 <= code < 400:
        return ProviderError(f"{_cap(who)} tried to redirect the request, so it wasn't sent.", status=code)
    return ProviderError(f"{_cap(who)} returned an error ({code}{f': {message}' if message else ''}).", status=code)


def _send(method: str, url: str, who: str, timeout: float | None = None, **kwargs) -> dict:
    try:
        with _client() as c:
            r = c.request(method, url, timeout=httpx.Timeout(timeout, connect=10) if timeout else TIMEOUT, **kwargs)
    except httpx.TimeoutException:
        raise ProviderError(f"{_cap(who)} took too long to answer.") from None
    except httpx.HTTPError:
        raise ProviderError(f"Couldn't reach {who}. Check the address and your connection.") from None
    if r.status_code != 200:
        raise _fail(r, who)
    try:
        return r.json()
    except ValueError:
        raise ProviderError(f"{_cap(who)} sent back something that isn't JSON.") from None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---- Claude


def _anthropic_headers(e: Endpoint) -> dict:
    return {"x-api-key": e.api_key or "", "anthropic-version": "2023-06-01"}


def _anthropic_models(e: Endpoint) -> list[str]:
    body = _send("GET", f"{ANTHROPIC_URL}/models?limit=100", "Claude", headers=_anthropic_headers(e))
    return [m["id"] for m in body.get("data", []) if isinstance(m, dict) and "id" in m]


def _optional_not_null(schema):
    """Claude's structured outputs cap the schema's complexity: 21 nullable fields in the reading schema are too many
    unions ("too many parameters with union types"), and 21 optional ones are too big a grammar ("Schema is too
    complex"; each optional field roughly doubles part of it). So for Claude a nullable field loses its null branch:
    a string or an object stays required (an empty string means not shown, which every reader treats like None), and
    only a number becomes optional (left out when not shown; the answer models default it to None).
    """
    if isinstance(schema, list):
        return [_optional_not_null(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if schema.get("type") == "object" and "properties" in schema:
        props, required = {}, []
        for name, sub in schema["properties"].items():
            branches = sub.get("anyOf") if isinstance(sub, dict) else None
            if branches and any(b == {"type": "null"} for b in branches):
                rest = [b for b in branches if b != {"type": "null"}]
                inner = rest[0] if len(rest) == 1 else {**sub, "anyOf": rest}
                if inner.get("type") == "string":
                    inner = {**inner, "description": "Empty if not shown."}
                props[name] = _optional_not_null(inner)
                keep = inner.get("type") in ("string", "object")
            else:
                props[name] = _optional_not_null(sub)
                keep = True
            if keep and name in schema.get("required", []):
                required.append(name)
        return {**schema, "properties": props, "required": required}
    return {k: _optional_not_null(v) for k, v in schema.items()}


# Models that take the newer web search tool (it filters results before reading them, so it costs fewer tokens).
FILTERING_SEARCH = re.compile(r"^claude-(opus-(4-[6-9]|5)|sonnet-(4-[6-9]|5))")
MAX_RESUMES = 3  # a long search can pause; it's picked up again this many times at most


def _search_tool(model: str, uses: int) -> dict:
    kind = "web_search_20260209" if FILTERING_SEARCH.match(model) else "web_search_20250305"
    return {"type": kind, "name": "web_search", "max_uses": uses}


def _anthropic_complete(e: Endpoint, model: str, req: Request) -> Reply:
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": p.mime, "data": _b64(p.data)}}
        for p in req.pictures
    ]
    content.append({"type": "text", "text": req.text})
    messages: list[dict] = [{"role": "user", "content": content}]
    payload: dict = {"model": model, "max_tokens": req.max_tokens, "system": req.system, "messages": messages}
    if req.web_searches:
        payload["tools"] = [_search_tool(model, req.web_searches)]
    elif req.schema:
        payload["output_config"] = {"format": {"type": "json_schema", "schema": _optional_not_null(req.schema)}}
    reply = Reply(text="", input_tokens=0, output_tokens=0)
    blocks: list[dict] = []
    for _ in range(MAX_RESUMES + 1):
        body = _send(
            "POST", f"{ANTHROPIC_URL}/messages", "Claude", req.timeout, headers=_anthropic_headers(e), json=payload
        )
        usage = body.get("usage") or {}
        reply.input_tokens += (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
        )
        reply.output_tokens += int(usage.get("output_tokens") or 0)
        reply.web_searches += int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
        got = [b for b in body.get("content") or [] if isinstance(b, dict)]
        blocks += got
        stop = body.get("stop_reason")
        if stop != "pause_turn":
            break
        # The search loop paused: send its turn back as it is and it carries on where it stopped.
        messages = [*messages[:1], {"role": "assistant", "content": blocks}]
        payload["messages"] = messages
    _collect_search(blocks, reply)
    # The answer is the text after the last search (before it, the model may say what it's about to look up).
    last_tool = max((i for i, b in enumerate(blocks) if b.get("type") != "text"), default=-1)
    reply.text = "".join(b.get("text", "") for b in blocks[last_tool + 1 :] if b.get("type") == "text")
    if stop == "refusal":
        raise ProviderError("Claude declined to read this one.", reply)
    if stop == "max_tokens":
        raise ProviderError("The answer was too long and got cut off. Try a shorter file.", reply)
    if stop == "pause_turn":
        raise ProviderError("Claude was still searching after several rounds. Try a narrower question.", reply)
    return reply


def _collect_search(blocks: list[dict], reply: Reply) -> None:
    """The pages web search found and the ones the answer cites (urls and titles only; nothing is fetched here)."""
    for b in blocks:
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if isinstance(r, dict) and r.get("type") == "web_search_result" and isinstance(r.get("url"), str):
                    reply.found.append(
                        {"url": r["url"], "title": str(r.get("title") or "")[:300], "age": str(r.get("page_age") or "")}
                    )
        for c in b.get("citations") or [] if b.get("type") == "text" else []:
            if isinstance(c, dict) and isinstance(c.get("url"), str):
                reply.cited.append(
                    {"url": c["url"], "title": str(c.get("title") or "")[:300], "quote": str(c.get("cited_text") or "")}
                )


# ---- OpenAI and OpenAI-compatible servers (Ollama, LM Studio, vLLM, OpenRouter...)


def _openai_base(e: Endpoint) -> str:
    return OPENAI_URL if e.kind == "openai" else check_base_url(e.base_url or "")


def _openai_headers(e: Endpoint) -> dict:
    return {"Authorization": f"Bearer {e.api_key}"} if e.api_key else {}


def _openai_who(e: Endpoint) -> str:
    return "OpenAI" if e.kind == "openai" else "your server"


def _openai_models(e: Endpoint) -> list[str]:
    body = _send("GET", f"{_openai_base(e)}/models", _openai_who(e), headers=_openai_headers(e))
    ids = [m["id"] for m in body.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if e.kind == "openai":
        ids = [i for i in ids if not any(x in i for x in NOT_CHAT)]
    return ids


def _openai_complete(e: Endpoint, model: str, req: Request) -> Reply:
    content: list[dict] = [{"type": "text", "text": req.text}]
    content += [
        {"type": "image_url", "image_url": {"url": f"data:{p.mime};base64,{_b64(p.data)}"}} for p in req.pictures
    ]
    payload: dict = {
        "model": model,
        "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": content}],
    }
    if e.kind == "openai":
        payload["max_completion_tokens"] = req.max_tokens
        if req.schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": req.schema, "strict": True},
            }
    else:
        # Local servers differ in what they accept: keep to the basics and ask for JSON in the prompt.
        payload["max_tokens"] = req.max_tokens
    who = _openai_who(e)
    body = _send(
        "POST", f"{_openai_base(e)}/chat/completions", who, req.timeout, headers=_openai_headers(e), json=payload
    )
    usage = body.get("usage") or {}
    choices = body.get("choices") or [{}]
    message = choices[0].get("message") or {}
    reply = Reply(
        text=message.get("content") or "",
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )
    if message.get("refusal"):
        raise ProviderError(f"{_cap(who)} declined to read this one.", reply)
    if choices[0].get("finish_reason") == "length":
        raise ProviderError("The answer was too long and got cut off. Try a shorter file.", reply)
    return reply


# ---- Gemini


def _gemini_headers(e: Endpoint) -> dict:
    return {"x-goog-api-key": e.api_key or ""}


def _gemini_models(e: Endpoint) -> list[str]:
    body = _send("GET", f"{GEMINI_URL}/models?pageSize=200", "Gemini", headers=_gemini_headers(e))
    out = []
    for m in body.get("models", []):
        if isinstance(m, dict) and "generateContent" in (m.get("supportedGenerationMethods") or []):
            out.append(str(m.get("name", "")).removeprefix("models/"))
    return [m for m in out if m]


def _gemini_complete(e: Endpoint, model: str, req: Request) -> Reply:
    parts: list[dict] = [{"inline_data": {"mime_type": p.mime, "data": _b64(p.data)}} for p in req.pictures]
    parts.append({"text": req.text})
    payload = {
        "systemInstruction": {"parts": [{"text": req.system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": req.max_tokens}
        | ({"responseMimeType": "application/json"} if req.schema else {}),
    }
    url = f"{GEMINI_URL}/models/{check_model(model)}:generateContent"
    body = _send("POST", url, "Gemini", req.timeout, headers=_gemini_headers(e), json=payload)
    usage = body.get("usageMetadata") or {}
    candidates = body.get("candidates") or [{}]
    reply = Reply(
        text="".join(p.get("text", "") for p in (candidates[0].get("content") or {}).get("parts", [])),
        input_tokens=int(usage.get("promptTokenCount") or 0),
        # Thinking tokens are billed as output.
        output_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
    )
    finish = candidates[0].get("finishReason")
    if finish == "MAX_TOKENS":
        raise ProviderError("The answer was too long and got cut off. Try a shorter file.", reply)
    if finish not in (None, "STOP"):
        raise ProviderError("Gemini declined to read this one.", reply)
    return reply


# ---- entry points


def list_models(e: Endpoint) -> list[str]:
    """The provider's models, newest-looking first. Also how a key is checked: listing costs nothing."""
    if e.kind == "anthropic":
        models = _anthropic_models(e)
    elif e.kind == "gemini":
        models = _gemini_models(e)
    elif e.kind in ("openai", "openai_compatible"):
        models = _openai_models(e)
    else:
        raise ProviderError("Unknown provider.")
    return [m for m in models if MODEL_RE.match(m)][:200]


def complete(e: Endpoint, model: str, req: Request) -> Reply:
    check_model(model)
    if e.kind == "anthropic":
        return _anthropic_complete(e, model, req)
    if e.kind == "gemini":
        return _gemini_complete(e, model, req)
    if e.kind in ("openai", "openai_compatible"):
        return _openai_complete(e, model, req)
    raise ProviderError("Unknown provider.")


def parse_json(text: str) -> dict:
    """The JSON object in a reply, tolerating ```json fences or a sentence around it (local models add them)."""
    text = text.strip()
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None
        try:
            value = json.loads(text[start : end + 1])
        except ValueError:
            raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None
    if not isinstance(value, dict):
        raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.")
    return value
