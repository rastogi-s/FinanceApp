"""The database tables. Money is integer cents (negative = money out), timestamps are UTC, dates are calendar days.

Secrets (aggregator tokens, TOTP seeds) are stored only as AES-GCM ciphertext (see crypto.py); passwords, session
tokens, setup and invite codes only as hashes. Schema changes go through migrations/ (Alembic).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {datetime: DateTime(timezone=True), dict: JSONB, Decimal: Numeric(24, 8)}


# ---- people and sign-in


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))  # owner | member
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    # The last 30-second TOTP step accepted, so a code can't be used twice.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    # The user handle passkeys are bound to: random, never the email.
    webauthn_user_id: Mapped[bytes] = mapped_column(LargeBinary(64))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=now)
    last_login_at: Mapped[datetime | None]


class Passkey(Base):
    __tablename__ = "passkeys"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    transports: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    name: Mapped[str] = mapped_column(String(80))
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)
    last_used_at: Mapped[datetime | None]


class Session(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    method: Mapped[str] = mapped_column(String(16))  # passkey | password
    created_at: Mapped[datetime] = mapped_column(default=now)
    last_seen_at: Mapped[datetime] = mapped_column(default=now)
    expires_at: Mapped[datetime]
    # Set by a fresh sign-in; sensitive actions (revoke all, removing a passkey) need it to be recent.
    reauth_at: Mapped[datetime] = mapped_column(default=now)


class Device(Base):
    """A browser that has signed in before, remembered by a long-lived random cookie (hash stored)."""

    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    first_seen_at: Mapped[datetime] = mapped_column(default=now)
    last_seen_at: Mapped[datetime] = mapped_column(default=now)


class Challenge(Base):
    """A short-lived, single-use step of a sign-in: a WebAuthn challenge, or 'password was right, now the code'."""

    __tablename__ = "challenges"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    challenge: Mapped[bytes | None] = mapped_column(LargeBinary)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(default=dict)  # hashes and ciphertext only, never a plain secret
    expires_at: Mapped[datetime]


class Invite(Base):
    """The first-run setup code (kind=setup) and invitations for a second person (kind=invite). Hash only."""

    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    name: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=now)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class AuditEvent(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(48))
    detail: Mapped[dict] = mapped_column(default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


# ---- money


class Connection(Base):
    """One login at one institution, through one connector (Plaid, SnapTrade, a file upload, or manual entry)."""

    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))  # plaid | snaptrade | file | manual
    institution: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str | None] = mapped_column(String(200))  # Plaid item id, SnapTrade authorization id
    token_enc: Mapped[str | None] = mapped_column(Text)  # access token / user secret, encrypted; None once revoked
    state: Mapped[dict] = mapped_column(default=dict)  # non-secret sync state, e.g. the transactions cursor
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | stale | revoked
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_synced_at: Mapped[datetime | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    institution: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(16))  # depository | credit | loan | investment | other
    subtype: Mapped[str | None] = mapped_column(String(40))
    mask: Mapped[str | None] = mapped_column(String(4))  # last 4 digits only, never the full number
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Only one source counts per real account (e.g. E*TRADE through SnapTrade and a CSV), so nothing is counted twice.
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the statement said, typed in to check the app against it (the Phase 1 gate: within 1%).
    statement_cents: Mapped[int | None] = mapped_column(BigInteger)
    statement_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Balance(Base):
    """An account's balance on a day. Credit and loan balances are stored as positive amounts owed."""

    __tablename__ = "balances"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    current_cents: Mapped[int] = mapped_column(BigInteger)
    available_cents: Mapped[int | None] = mapped_column(BigInteger)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # fixed | flexible | income | transfer | other
    sort: Mapped[int] = mapped_column(Integer, default=100)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id"),
        Index("ix_transactions_date", "date"),
        Index("ix_transactions_transfer_group", "transfer_group_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)  # negative = money out of the account
    merchant: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(300))
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_category: Mapped[str | None] = mapped_column(String(80))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    # How the category was chosen: user | rule | builtin | provider | transfer | none. "user" is never overwritten.
    category_source: Mapped[str] = mapped_column(String(16), default="none")
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(16))  # plaid | snaptrade | file | manual
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class MerchantRule(Base):
    """'Always put this merchant in that category.' Made when someone fixes a transaction's category."""

    __tablename__ = "merchant_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200), unique=True)  # a normalized merchant name
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    created_from_txn_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class HoldingSnapshot(Base):
    __tablename__ = "holdings_snapshots"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    asset_class: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal]
    price: Mapped[Decimal]  # dollars per share
    value_cents: Mapped[int] = mapped_column(BigInteger)
    cost_basis_cents: Mapped[int | None] = mapped_column(BigInteger)


class SyncRun(Base):
    """One run of the sync, kept so 'seven nights in a row with no manual fix' can be checked."""

    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual
    started_at: Mapped[datetime] = mapped_column(default=now, index=True)
    finished_at: Mapped[datetime | None]
    ok: Mapped[bool | None]
    summary: Mapped[dict] = mapped_column(default=dict)


class Import(Base):
    """An uploaded file: parsed into a preview first, saved only when someone confirms it."""

    __tablename__ = "imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)
    filename: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24))  # positions | transactions
    status: Mapped[str] = mapped_column(String(16), default="preview")  # preview | committed | discarded
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    parsed: Mapped[dict] = mapped_column(default=dict)
    summary: Mapped[dict] = mapped_column(default=dict)
