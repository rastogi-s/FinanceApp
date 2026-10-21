"""Reading an upload with AI, in two steps so nothing is sent without a yes: first an offer (what would be sent, to
whom, at most what cost), then, when the person agrees and uploads again, the reading itself. The file is never
stored in between: the browser still has it.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp.ai import extract, service
from financeapp.ai.documents import Document
from financeapp.ai.extract import Answer, Outgoing
from financeapp.ai.providers import is_local_host
from financeapp.ai.redact import describe
from financeapp.db import Database
from financeapp.models import User

TASK = "read_files"
PREVIEW_CHARS = 1500


def household_identity(db: DbSession) -> tuple[list[str], list[str]]:
    """The household's names and emails, which are taken out of anything sent."""
    rows = db.execute(select(User.name, User.email)).all()
    return [r.name for r in rows] + service.settings(db)["hide_words"], [r.email for r in rows]


def outgoing_for(db: DbSession, doc: Document, hint: str = "") -> Outgoing:
    names, emails = household_identity(db)
    return extract.build_request(doc, names, emails, hint)


def offer(db: DbSession, doc: Document, out: Outgoing, reason: str) -> dict:
    """What would happen if the person says yes. `available` is False (with the reason) when it can't."""
    pictures = bool(out.request.pictures)
    body: dict = {
        "reason": reason,
        "sends": "pictures" if pictures else "text",
        "pages": len(out.request.pictures) if pictures else doc.pages,
        "truncated": doc.truncated,
        "preview": out.redacted.text[:PREVIEW_CHARS] if out.redacted else None,
        "redactions": describe(out.redacted.counts) if out.redacted else [],
        "provider": None,
        "model": None,
        "local": False,
        "max_cost": None,
        "available": False,
        "problem": None,
    }
    route, problem = service.find_route(db, TASK, pictures=pictures)
    if route is not None:
        cost = service.estimate(out.request, route.price)
        problem = service.cap_problem(db, cost)
        body |= {
            "provider": route.provider.label,
            "model": route.model,
            "local": route.provider.kind == "openai_compatible"
            and is_local_host(urlsplit(route.provider.base_url or "").hostname or ""),
            "max_cost": round(cost / 1_000_000, 4),
        }
    body["available"] = problem is None
    body["problem"] = problem
    return body


@dataclass
class Read:
    answer: Answer
    model: str
    cost: float  # dollars
    names: list[str]
    emails: list[str]


def read(database: Database, db: DbSession, user_id: int, doc: Document, hint: str = "") -> Read:
    """Sends the document. Raises AIUnavailable (nothing sent) or ProviderError (sent, failed, logged)."""
    names, emails = household_identity(db)
    out = extract.build_request(doc, names, emails, hint)
    reply, route = service.run(database, db, TASK, user_id, out.request)
    cost = service.microdollars(reply.input_tokens, reply.output_tokens, route.price) / 1_000_000
    return Read(extract.parse_answer(reply.text), route.model, round(cost, 4), names, emails)
