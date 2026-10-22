"""The database tables. Money is integer cents (negative = money out), timestamps are UTC, dates are calendar days.

Secrets (aggregator tokens, TOTP seeds, AI keys) are stored only as AES-GCM ciphertext (see crypto.py); passwords,
session tokens, setup and invite codes only as hashes. Schema changes go through migrations/ (Alembic).
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
    false,
    text,
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
    # Counts toward the emergency fund target (every bill, plus groceries, transport and health to start with).
    essential: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


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
    # Stock plan shares not vested yet. Left out of quantity, value and cost basis; shown on the holding only.
    unvested_quantity: Mapped[Decimal | None]


class InvestmentTransaction(Base):
    """A brokerage account's activity: buys, sells, dividends, interest, fees. Kept for dividend and interest income
    (income.py). amount_cents follows the app's convention: > 0 is cash arriving in the account (a dividend paid)."""

    __tablename__ = "investment_transactions"
    __table_args__ = (UniqueConstraint("account_id", "external_id"), Index("ix_investment_transactions_date", "date"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    date: Mapped[date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String(24))  # buy | sell | cash | fee | transfer | cancel
    subtype: Mapped[str] = mapped_column(String(48))  # e.g. "qualified dividend", "interest", "dividend reinvestment"
    symbol: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal | None]
    price: Mapped[Decimal | None]
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))  # plaid
    created_at: Mapped[datetime] = mapped_column(default=now)


class DividendHistory(Base):
    """A symbol's dividends per share by ex-dividend date, from the quote service (only the ticker is sent)."""

    __tablename__ = "dividend_history"
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True)
    amount: Mapped[Decimal]  # dollars per share


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


# ---- budget and pay


class BudgetVersion(Base):
    """One saved budget. Every save makes a new version, so an older one can be looked at or brought back."""

    __tablename__ = "budget_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # draft | edit | restore | ai
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class BudgetLine(Base):
    """A category's monthly limit in one version of the budget."""

    __tablename__ = "budget_lines"
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_versions.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    limit_cents: Mapped[int] = mapped_column(BigInteger)


class Paycheck(Base):
    """A pay stub, typed in. Other deductions (health insurance and the like) = gross - taxes - savings - net."""

    __tablename__ = "paychecks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))  # whose pay
    # Before "date", which would otherwise hide the date type for these annotations.
    frequency: Mapped[str | None] = mapped_column(String(16))  # weekly | biweekly | semimonthly | monthly
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    date: Mapped[date] = mapped_column(Date, index=True)  # pay date
    gross_cents: Mapped[int] = mapped_column(BigInteger)
    taxes_cents: Mapped[int] = mapped_column(BigInteger)  # federal, state, Social Security and Medicare
    savings_cents: Mapped[int] = mapped_column(BigInteger)  # 401(k), HSA and ESPP taken out before pay
    net_cents: Mapped[int] = mapped_column(BigInteger)  # what landed in the bank
    employer_match_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # {"this": {...}, "ytd": {...}}: federal, state, social_security, medicare, retirement (pre-tax), hsa, benefits,
    # stock... in cents, as far as the stub showed them (paystub.DETAIL); "ytd" also has "gross". Read by taxes.py.
    detail: Mapped[dict] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class Goal(Base):
    """Something the household is saving for: a house down payment, a car, the emergency fund (always there, one of
    it). Money counts toward a goal through goal_funding; see goals.py for how a balance is shared out."""

    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # emergency | saving
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(16))
    color: Mapped[str] = mapped_column(String(16))
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 high, 2 medium, 3 low
    # The emergency fund's target is months of essentials (the budget's setting) unless one is typed in.
    target_cents: Mapped[int | None] = mapped_column(BigInteger)
    target_date: Mapped[date | None] = mapped_column(Date)
    rate_percent: Mapped[Decimal | None]  # what the money earns a year; None: the app's default
    monthly_cents: Mapped[int | None] = mapped_column(BigInteger)  # what goes in each month; None: the app works it out
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class GoalFunding(Base):
    """Money in an account set aside for a goal: a fixed amount of it, or (cents None) whatever is left of it."""

    __tablename__ = "goal_funding"
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True, index=True)
    cents: Mapped[int | None] = mapped_column(BigInteger)


class Preference(Base):
    """Household settings that aren't secrets or server config, e.g. how many months the emergency fund covers."""

    __tablename__ = "preferences"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(default=dict)


# ---- AI


class AIProvider(Base):
    """An AI provider the owner set up. The key is stored only as ciphertext and never sent back to the browser."""

    __tablename__ = "ai_providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # anthropic | openai | gemini | openai_compatible
    label: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str | None] = mapped_column(String(300))  # openai_compatible only
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    key_hint: Mapped[str | None] = mapped_column(String(8))  # the key's last 4 characters, to tell keys apart
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Pictures and scans can't be redacted before sending, so they only go to a provider with this switched on.
    allow_pictures: Mapped[bool] = mapped_column(Boolean, default=False)
    models: Mapped[dict] = mapped_column(default=dict)  # {"ids": [...]} from the last successful check
    last_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class AIUsage(Base):
    """One call to an AI provider: who, what for, which model, tokens and cost. Never what was sent or answered."""

    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    task: Mapped[str] = mapped_column(String(24))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("ai_providers.id", ondelete="SET NULL"))
    provider_kind: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_microdollars: Mapped[int] = mapped_column(BigInteger, default=0)  # millionths of a dollar
    priced: Mapped[bool] = mapped_column(Boolean, default=True)  # False: the model's price was a guess
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(String(200))


# ---- prices, alerts and look-through


class Quote(Base):
    """A symbol's latest price from the quote service. Only ticker symbols are ever sent to it."""

    __tablename__ = "quotes"
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    price: Mapped[Decimal]
    previous_close: Mapped[Decimal | None]
    as_of: Mapped[datetime]  # the market time of the price
    source: Mapped[str] = mapped_column(String(16))  # finnhub | yahoo
    fetched_at: Mapped[datetime] = mapped_column(default=now)
    # From a year of daily closes, refreshed once a day after the close (quotes.refresh_trends).
    year_high: Mapped[Decimal | None]
    year_low: Mapped[Decimal | None]
    average_50: Mapped[Decimal | None]
    average_200: Mapped[Decimal | None]
    trend_as_of: Mapped[date | None]  # the trading day of the last close counted
    closes: Mapped[list | None] = mapped_column(JSONB)  # a month of daily closes, oldest first (the sparkline)


class AlertRule(Base):
    """A price, dip, budget, concentration or goal rule (see alerts.py for the params of each)."""

    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # price | dip | budget | concentration | goal
    params: Mapped[dict] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Which conditions have fired and not re-armed yet, so a rule never sends the same thing twice.
    state: Mapped[dict] = mapped_column(default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)


class AlertEvent(Base):
    """One alert. The dedup key is unique, so the same alert can't be saved (or sent) twice."""

    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(String(300))
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True)
    fired_at: Mapped[datetime] = mapped_column(default=now, index=True)
    # pending (to send) | held (quiet hours) | sent | unsent (no channel set up) | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str | None] = mapped_column(String(16))  # ntfy | email
    sent_at: Mapped[datetime | None]
    read_at: Mapped[datetime | None]


class Fund(Base):
    """A fund or ETF someone holds, and where its list of holdings came from."""

    __tablename__ = "funds"
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    series_id: Mapped[str | None] = mapped_column(String(16))  # the SEC's id for the fund, e.g. S000002839
    # nport (the fund's own filing) | proxy (a fund tracking the same index, e.g. SPY files none) | none
    source: Mapped[str] = mapped_column(String(16), default="none")
    proxy_of: Mapped[str | None] = mapped_column(String(40))
    accession: Mapped[str | None] = mapped_column(String(24))  # the filing the constituents came from
    as_of: Mapped[date | None] = mapped_column(Date)  # the date the holdings are as of
    equity_weight: Mapped[Decimal | None]  # share of the fund in stocks (0.99 for an index fund, 0 for bonds)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | ok | not_found | error
    error: Mapped[str | None] = mapped_column(String(200))
    fetched_at: Mapped[datetime | None]


class FundConstituent(Base):
    """One holding inside a fund, as a share of the fund's net assets."""

    __tablename__ = "fund_constituents"
    fund_symbol: Mapped[str] = mapped_column(ForeignKey("funds.symbol", ondelete="CASCADE"), primary_key=True)
    # The company (cik:320193), else the security (cusip:037833100), else the name; one row per key.
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    weight: Mapped[Decimal] = mapped_column(Numeric(14, 10))  # 0.07 = 7% of the fund
    is_fund: Mapped[bool] = mapped_column(Boolean, default=False)  # a fund inside the fund (target-date funds)


class SecurityId(Base):
    """CUSIP to ticker and company, looked up once and kept."""

    __tablename__ = "security_ids"
    cusip: Mapped[str] = mapped_column(String(9), primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(40))
    cik: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime] = mapped_column(default=now)


# ---- agents (the read-only MCP server) and the morning brief


class AgentKey(Base):
    """A key an AI agent uses to read the household's numbers over MCP. Only its SHA-256 is stored; the key itself
    is shown once, when it's made. Keys can only read: the MCP server has no tools that change anything."""

    __tablename__ = "agent_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    hint: Mapped[str] = mapped_column(String(8))  # the key's last 4 characters, to tell keys apart
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=now)
    last_used_at: Mapped[datetime | None]
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None]


class Brief(Base):
    """One morning brief: the numbers it was built from, and what it says."""

    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)  # in the household's time zone
    facts: Mapped[dict] = mapped_column(default=dict)
    headline: Mapped[str] = mapped_column(String(200))
    points: Mapped[dict] = mapped_column(default=dict)  # {"items": [...]}
    written_by: Mapped[str] = mapped_column(String(80))  # "app", or the AI model that wrote it
    channel: Mapped[str | None] = mapped_column(String(16))  # ntfy | email, when it was sent
    created_at: Mapped[datetime] = mapped_column(default=now)
    read_at: Mapped[datetime | None]
