"""The setup guide: the few things that make the app useful, in order, each ticked off by what's in the database.

Nothing is ticked by hand (except hiding the guide): a step is done when the thing it asks for exists, so the
guide can't drift from the truth, and a step undone later (the last passkey removed) shows up again. Four steps
are needed; the rest make the numbers better and can be skipped.
"""

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import notify, plan
from financeapp.config import Settings
from financeapp.models import (
    Account,
    AIProvider,
    BudgetVersion,
    Connection,
    Invite,
    Passkey,
    Paycheck,
    Preference,
    User,
    now,
)

# id, title, why, where to go, the button, needed (False: makes the numbers better, can be skipped), owner only
STEPS = [
    (
        "passkey",
        "Sign in with Face ID",
        "Add a passkey so signing in is a glance, and a stolen password alone gets no one in.",
        "/settings/security",
        "Add a passkey",
        True,
        False,
    ),
    (
        "accounts",
        "Link your accounts",
        "Banks, cards and brokerages, one at a time. Anything that won't link can come in as a file.",
        "/settings/accounts?welcome=1",
        "Add an account",
        True,
        False,
    ),
    (
        "budget",
        "Check the budget",
        "The app drafts one from your last few months. Look it over and save it, or change any line.",
        "/budget",
        "Review the budget",
        True,
        False,
    ),
    (
        "alerts",
        "Get alerts on your phone",
        "Send a test to make sure price, budget and security alerts reach you.",
        "/settings/alerts",
        "Send a test alert",
        True,
        False,
    ),
    (
        "paycheck",
        "Add a pay stub",
        "So the savings rate counts your 401(k) and taxes, not only take-home pay.",
        "/budget#pay",
        "Add a pay stub",
        False,
        False,
    ),
    (
        "plan",
        "Set up your plan",
        "Your age and the spending you'd like later, for a date when work becomes optional.",
        "/plan",
        "Open the plan",
        False,
        False,
    ),
    (
        "people",
        "Invite your partner",
        "They get their own sign-in and see the same household.",
        "/settings/people",
        "Invite someone",
        False,
        True,
    ),
    (
        "ai",
        "Connect an AI provider",
        "Reads statements and pay stubs no built-in reader knows. Off until you add one.",
        "/settings/ai",
        "Set up AI",
        False,
        True,
    ),
]


def _any(db: DbSession, query) -> bool:
    return bool(db.scalar(select(exists(query))))


def _steps(done: dict[str, bool], waiting: dict[str, str], owner: bool, can_test: bool) -> list[dict]:
    return [
        {
            "id": key,
            "title": title,
            "body": body,
            "href": href,
            "action": action,
            "optional": not needed,
            "done": done.get(key, False),
            "waiting": None if done.get(key) else waiting.get(key),
            "test": key == "alerts" and can_test,
        }
        for key, title, body, href, action, needed, owner_only in STEPS
        if owner or not owner_only
    ]


def steps(db: DbSession, settings: Settings, user: User) -> list[dict]:
    linked = _any(
        db,
        select(Account.id).join(Connection).where(Connection.status != "revoked", Account.hidden.is_(False)),
    )
    people = db.scalar(select(func.count()).select_from(User)) or 0
    done = {
        "passkey": _any(db, select(Passkey.id).where(Passkey.user_id == user.id)),
        "accounts": linked,
        "budget": _any(db, select(BudgetVersion.id)),
        "alerts": db.get(Preference, "alert_test") is not None,
        "paycheck": _any(db, select(Paycheck.id)),
        "plan": plan.preferences(db)["birth_year"] is not None,
        "people": people > 1 or _any(db, select(Invite.id).where(Invite.kind == "invite", Invite.expires_at > now())),
        "ai": _any(db, select(AIProvider.id).where(AIProvider.enabled.is_(True))),
    }
    waiting = {}
    if not linked:
        waiting["budget"] = "Link an account first."
    if not notify.configured(settings):
        waiting["alerts"] = "Set NTFY_URL (or the email settings) in the server's .env file, then come back."
    return _steps(done, waiting, user.role == "owner", notify.configured(settings))


def summary(s: list[dict], hidden: bool) -> dict:
    needed = [x for x in s if not x["optional"]]
    return {
        "steps": s,
        "needed": len(needed),
        "needed_done": sum(1 for x in needed if x["done"]),
        "done": sum(1 for x in s if x["done"]),
        "complete": all(x["done"] for x in needed),
        "hidden": hidden,
    }


def state(db: DbSession, settings: Settings, user: User) -> dict:
    p = db.get(Preference, "guide")
    return summary(steps(db, settings, user), bool(p and p.value.get("hidden")))


def demo_state() -> dict:
    done = dict.fromkeys(["passkey", "accounts", "budget", "alerts", "paycheck"], True)
    return summary(_steps(done, {}, owner=True, can_test=False), hidden=False)
