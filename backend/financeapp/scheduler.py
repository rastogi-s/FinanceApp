"""The background work, in a thread inside the app (no extra container): the sync every SYNC_INTERVAL_HOURS, live
prices every QUOTE_INTERVAL_MINUTES while markets are open (each followed by a check of the price and concentration
alerts), funds' holdings for look-through once a week, and whether a newer version is out every few hours.

It checks every minute whether each is due, from what's saved in the database, so restarts and updates don't cause
extra runs, and the sync's own database lock means two copies of the app never sync at once. It also clears out
expired sessions and sign-in steps.
"""

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from financeapp import alerts, lookthrough, quotes, sync, updates
from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import Challenge, Connection, Session, SyncRun, now

log = logging.getLogger("financeapp")
CHECK_SECONDS = 60
FUNDS_CHECK = timedelta(hours=6)


class Scheduler:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="sync-scheduler", daemon=True)
        self.funds_checked: datetime | None = None

    def start(self) -> None:
        self.thread.start()

    def shutdown(self) -> None:
        self.stop.set()

    def _due(self) -> bool:
        with self.database.session() as db:
            if db.scalar(select(Connection.id).where(Connection.provider.in_(["plaid", "snaptrade"])).limit(1)) is None:
                return False
            last = db.scalar(
                select(SyncRun.started_at).where(SyncRun.trigger == "schedule").order_by(SyncRun.started_at.desc())
            )
        return last is None or last < now() - timedelta(hours=self.settings.sync_interval_hours)

    def tick(self) -> None:
        with self.database.session() as db:
            db.execute(delete(Session).where(Session.expires_at < now()))
            db.execute(delete(Challenge).where(Challenge.expires_at < now()))
        if self._due():
            run = sync.run(self.database, self.settings, "schedule")
            if run is not None:
                log.info("scheduled sync finished: %s", "ok" if run.ok else "with problems")
        if quotes.refresh(self.database, self.settings) is not None:
            alerts.check(self.database, self.settings, {"price", "concentration"})
        else:
            # Held alerts go out when quiet hours end, even if no new prices came in.
            with self.database.session() as db:
                alerts.send_pending(db, self.settings)
        updates.check_if_due(self.database, self.settings)
        if self.funds_checked is None or now() - self.funds_checked > FUNDS_CHECK:
            self.funds_checked = now()
            if lookthrough.refresh(self.database, self.settings):
                alerts.check(self.database, self.settings, {"concentration"})

    def _loop(self) -> None:
        delay = 60  # let the app finish starting first
        while not self.stop.wait(delay):
            delay = CHECK_SECONDS
            try:
                self.tick()
            except Exception:
                log.exception("background sync check failed")
