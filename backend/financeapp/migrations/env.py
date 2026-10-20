"""Alembic environment: migrations run on the connection the app hands over (see Database.migrate)."""

from alembic import context

from financeapp.models import Base

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
