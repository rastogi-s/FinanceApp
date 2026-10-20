"""The background sync: every SYNC_INTERVAL_HOURS, in a thread inside the app (no extra container).

It checks every few minutes whether the last scheduled sync is old enough, so restarts and updates don't cause
extra syncs, and the sync's own database lock means two copies of the app never sync at once. It also clears out
expired sessions and sign-in steps.
"""

import logging
import threading
from datetime import timedelta

from sqlalchemy import delete, select

from financeapp import sync
from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import Challenge, Connection, Session, SyncRun, now

log = logging.getLogger("financeapp")
CHECK_SECONDS = 300


class Scheduler:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="sync-scheduler", daemon=True)

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

    def _loop(self) -> None:
        delay = 60  # let the app finish starting first
        while not self.stop.wait(delay):
            delay = CHECK_SECONDS
            try:
                self.tick()
            except Exception:
                log.exception("background sync check failed")
