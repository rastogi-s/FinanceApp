"""Upload reminders for accounts with no automatic connection: ones filled from uploaded files, or typed in by hand.
Nothing comes in for them unless someone uploads a statement or a transactions download, so the app says when the
next one should be out.

How often, per account (saved in the "upload_reminders" preference, by account id):
  monthly    the default: after each statement. The statement closes on `day` (1 to 31, the last day of a shorter
             month for 29 to 31; month end when not set), and the reminder comes GRACE_DAYS later, when it's
             normally out to download.
  quarterly  the same, for a statement every three months (some 401(k)s and HSAs)
  weekly     a week after the newest data
  off        never. Typed-in "something you own" accounts (a house, a car) start here.

The newest data is the latest transaction or balance date in the account. A reminder is due once the next statement
after that should be out, until something newer is uploaded or typed in; "Later" snoozes it for a few days.

Pushes go through the alerts queue (alerts.send_pending): quiet hours, grouping and the ntfy/email fallback all
apply, at normal priority so a Focus mode can hold them back. One push when an upload falls due and one more a
week later if it's still missing; after that it stays on Home and in Settings → Accounts without nagging. The push
names the account and the month, never a balance.
"""

import calendar
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp.alerts import quiet_hours, zone
from financeapp.models import Account, AlertEvent, Balance, Connection, Preference, Transaction, now

PREF = "upload_reminders"
EVERY = ("monthly", "quarterly", "weekly", "off")
GRACE_DAYS = 3  # a statement is normally out to download a few days after it closes
AGAIN_DAYS = 7  # the one follow-up push, if nothing new came in
SNOOZE_DAYS = (1, 3, 7)
PROVIDERS = ("file", "manual")
MONTH = "%B"


def statement_on(year: int, month: int, day: int | None) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day or last, last))


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    m = month - 1 + n
    return year + m // 12, m % 12 + 1


def next_statement(every: str, day: int | None, newest: date) -> date:
    """The first statement after the newest data; for quarterly, the one three months on."""
    if every == "weekly":
        return newest + timedelta(days=7)
    if every == "quarterly":
        return statement_on(*add_months(newest.year, newest.month, 3), day)
    s = statement_on(newest.year, newest.month, day)
    return s if s > newest else statement_on(*add_months(newest.year, newest.month, 1), day)


def due_on(every: str, day: int | None, newest: date) -> date:
    s = next_statement(every, day, newest)
    return s if every == "weekly" else s + timedelta(days=GRACE_DAYS)


def default_every(a: Account, provider: str) -> str:
    return "off" if provider == "manual" and a.type == "other" else "monthly"


def prefs(db: DbSession) -> dict:
    p = db.get(Preference, PREF)
    return dict(p.value) if p else {}


def save_pref(db: DbSession, account_id: int, **changes) -> None:
    p = db.get(Preference, PREF)
    if p is None:
        p = Preference(key=PREF, value={})
        db.add(p)
    value = dict(p.value)
    value[str(account_id)] = {**value.get(str(account_id), {}), **changes}
    p.value = value  # a new dict, so SQLAlchemy sees the change


def accounts(db: DbSession) -> list[tuple[Account, Connection]]:
    """Accounts that only get new data by hand: uploaded or typed in, counted, not hidden."""
    rows = db.execute(
        select(Account, Connection)
        .join(Connection, Account.connection_id == Connection.id)
        .where(
            Connection.provider.in_(PROVIDERS),
            Connection.status != "revoked",
            Account.hidden.is_(False),
            Account.is_authoritative.is_(True),
        )
        .order_by(Account.name)
    )
    return [(a, c) for a, c in rows]


def newest_data(db: DbSession, ids: list[int]) -> dict[int, date]:
    if not ids:
        return {}
    out: dict[int, date] = {}
    for model in (Transaction, Balance):
        q = select(model.account_id, func.max(model.date)).where(model.account_id.in_(ids)).group_by(model.account_id)
        for account_id, d in db.execute(q):
            if d and (account_id not in out or d > out[account_id]):
                out[account_id] = d
    return out


def status(db: DbSession, today: date) -> list[dict]:
    """Every hand-fed account with its reminder: how often, the newest data, when the next upload is due."""
    rows = accounts(db)
    saved = prefs(db)
    newest = newest_data(db, [a.id for a, _ in rows])
    out = []
    for a, c in rows:
        p = saved.get(str(a.id), {})
        every = p.get("every") if p.get("every") in EVERY else default_every(a, c.provider)
        day = p.get("day") if isinstance(p.get("day"), int) and 1 <= p["day"] <= 31 else None
        snoozed = date.fromisoformat(p["snoozed_until"]) if p.get("snoozed_until") else None
        latest = newest.get(a.id) or a.created_at.date()
        statement = next_statement(every, day, latest) if every != "off" else None
        due = due_on(every, day, latest) if every != "off" else None
        is_due = due is not None and today >= due
        out.append(
            {
                "account_id": a.id,
                "connection_id": c.id,
                "name": _name(a),
                "institution": c.institution,
                "provider": c.provider,
                "type": a.type,
                "every": every,
                "day": day,
                "newest": latest.isoformat(),
                "statement": statement.isoformat() if statement else None,
                "due": due.isoformat() if due else None,
                "is_due": is_due,
                "days_late": (today - due).days if is_due else 0,
                "snoozed_until": snoozed.isoformat() if snoozed and snoozed > today else None,
            }
        )
    return out


def _name(a: Account) -> str:
    from financeapp.household import display_name  # household imports plan, which is heavy; only when needed

    return display_name(a)


def due(db: DbSession, today: date) -> list[dict]:
    """What Home nudges about: due and not snoozed, the longest waiting first."""
    rows = [r for r in status(db, today) if r["is_due"] and not r["snoozed_until"]]
    return sorted(rows, key=lambda r: -r["days_late"])


def wording(r: dict) -> tuple[str, str]:
    """A push's title and body: the account and the month, never an amount."""
    title = f"Time to upload {r['name']}"
    if r["every"] == "weekly":
        body = "It's been a week since its newest transactions."
    elif r["provider"] == "manual":
        body = "Its balance hasn't been updated since its last statement. Type in the new one."
    else:
        month = date.fromisoformat(r["statement"]).strftime(MONTH)
        body = f"Its {month} statement should be out by now."
    if r["provider"] == "file":
        body += " Upload it, or a transactions download, to keep Spending and Budget current."
    return title[:120], body[:300]


def queue(db: DbSession, today: date) -> int:
    """Saves a push for every upload that just fell due, and the one follow-up a week later. The dedup key names the
    account and the due date, so each is saved once however often this runs. Returns how many were added."""
    added = 0
    for r in due(db, today):
        round_ = 2 if r["days_late"] >= AGAIN_DAYS else 1
        title, body = wording(r)
        stmt = insert(AlertEvent).values(
            rule_id=None,
            kind="upload",
            title=title,
            body=body,
            dedup_key=f"upload:{r['account_id']}:{r['due']}:{round_}",
            fired_at=now(),
            status="pending",
            attempts=0,
            link=f"/settings/accounts?account={r['account_id']}",
        )
        if db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None:
            added += 1
    return added


def snooze(db: DbSession, account_id: int, days: int, today: date) -> None:
    save_pref(db, account_id, snoozed_until=(today + timedelta(days=days)).isoformat())


def uploaded(db: DbSession, account_ids: list[int]) -> None:
    """New data came in by hand: a snooze is over (the next reminder is worked out from the new data)."""
    saved = prefs(db)
    for i in account_ids:
        if saved.get(str(i), {}).get("snoozed_until"):
            save_pref(db, i, snoozed_until=None)


def local_today(db: DbSession, t: datetime | None = None) -> date:
    return (t or now()).astimezone(zone(quiet_hours(db)["timezone"])).date()
