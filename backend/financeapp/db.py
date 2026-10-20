"""Database connection, sessions, and schema migrations (Alembic, run at startup)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger("financeapp")
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
# Any number works as long as it's the same everywhere: it stops two app containers migrating at once.
MIGRATION_LOCK = 72_416_001


class Database:
    def __init__(self, url: str) -> None:
        self.engine: Engine = create_engine(
            url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"connect_timeout": 3}
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def ok(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("select 1"))
            return True
        except Exception:
            return False

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A unit of work: committed if the block finishes, rolled back if it raises."""
        with self.sessions() as s:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    def migrate(self) -> None:
        """Brings the schema up to this version. If the database is from a newer version (after going back with
        update.sh), it is left alone: migrations only ever add, so older code still runs against it."""
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS))
        script = ScriptDirectory.from_config(config)
        with self.engine.begin() as conn:
            conn.execute(text("select pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
            current = MigrationContext.configure(conn).get_current_revision()
            if current is not None and current not in {r.revision for r in script.walk_revisions()}:
                log.warning("The database schema (%s) is newer than this version; not migrating.", current)
                return
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
