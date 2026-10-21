"""The connector interface and the plain data every connector returns.

Sign convention: amount_cents < 0 is money leaving the account (a purchase, a payment), > 0 is money arriving.
Credit card and loan balances are positive amounts owed. Only the last 4 digits of an account number are kept.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


class ConnectorError(Exception):
    """A provider said no. `needs_relink` means the person has to sign in at the institution again."""

    def __init__(self, message: str, code: str = "", needs_relink: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.needs_relink = needs_relink


@dataclass
class AccountData:
    external_id: str
    name: str
    institution: str
    type: str  # depository | credit | loan | investment | other
    subtype: str | None
    mask: str | None
    current_cents: int | None
    available_cents: int | None = None


@dataclass
class HoldingData:
    account_external_id: str
    symbol: str
    name: str
    asset_class: str
    quantity: Decimal
    price: Decimal
    value_cents: int
    cost_basis_cents: int | None = None


@dataclass
class TransactionData:
    account_external_id: str
    external_id: str
    date: date
    amount_cents: int
    merchant: str
    description: str
    pending: bool = False
    provider_category: str | None = None


@dataclass
class InvestmentTransactionData:
    """Brokerage activity. amount_cents > 0 is cash arriving in the account (a dividend), < 0 cash leaving (a buy)."""

    account_external_id: str
    external_id: str
    date: date
    type: str  # buy | sell | cash | fee | transfer | cancel
    subtype: str
    symbol: str | None
    name: str
    quantity: Decimal | None
    price: Decimal | None
    amount_cents: int


@dataclass
class FetchResult:
    accounts: list[AccountData] = field(default_factory=list)
    holdings: list[HoldingData] | None = None  # None: this source has no holdings (don't clear existing ones)
    transactions: list[TransactionData] = field(default_factory=list)
    removed_transaction_ids: list[str] = field(default_factory=list)
    investment_transactions: list[InvestmentTransactionData] = field(default_factory=list)
    state: dict = field(default_factory=dict)  # saved and handed back next time (e.g. a sync cursor)


@dataclass
class LinkStart:
    url: str  # where to send the browser: the provider's own hosted page, so no provider script runs in the app
    pending: dict  # kept server-side (encrypted) until the person comes back


@dataclass
class Linked:
    token: str  # the long-lived credential; stored encrypted
    external_id: str
    institution: str


class Connector(Protocol):
    provider: str

    def start_link(self, user_ref: str, return_url: str, kind: str) -> LinkStart: ...

    def finish_link(self, pending: dict) -> Linked: ...

    def fetch(self, token: str, external_id: str, state: dict) -> FetchResult: ...

    def revoke(self, token: str, external_id: str) -> None: ...


def cents(value: float | Decimal | str | None) -> int | None:
    if value is None or value == "":
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal(1)))


def last4(value: str | None) -> str | None:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits[-4:] if digits else None


def asset_class_for(kind: str | None, symbol: str = "") -> str:
    k = (kind or "").lower()
    if "cash" in k or "money market" in k or symbol.upper().endswith("**"):
        return "Cash"
    if "fixed" in k or "bond" in k:
        return "Bonds"
    if "etf" in k or "mutual" in k or "fund" in k:
        return "Funds"
    if "crypto" in k:
        return "Crypto"
    if "option" in k or "derivative" in k:
        return "Options"
    return "Stocks"
