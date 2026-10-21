"""Plaid: banks, cards and (with Investments) brokerages and retirement accounts.

Read-only products only (Transactions, Investments, Liabilities, Balance); nothing that can move money is ever
requested. Linking uses Plaid Hosted Link: the browser goes to Plaid's own page and comes back, so no Plaid script
runs inside the app and its Content-Security-Policy stays closed. The app isn't on the internet, so instead of
webhooks it asks for new transactions on every sync (/transactions/sync with a saved cursor).
"""

from datetime import date
from decimal import Decimal

import httpx

from financeapp.connectors.base import (
    AccountData,
    ConnectorError,
    FetchResult,
    HoldingData,
    Linked,
    LinkStart,
    TransactionData,
    asset_class_for,
    cents,
    last4,
)

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
RELINK_CODES = {
    "ITEM_LOGIN_REQUIRED",
    "PENDING_EXPIRATION",
    "PENDING_DISCONNECT",
    "ACCESS_NOT_GRANTED",
    "NO_ACCOUNTS",
    "ITEM_NOT_FOUND",
    "INVALID_ACCESS_TOKEN",
}
NOT_APPLICABLE = {
    "PRODUCTS_NOT_SUPPORTED",
    "NO_INVESTMENT_ACCOUNTS",
    "NO_INVESTMENT_AUTH_ACCOUNTS",
    "PRODUCT_NOT_ENABLED",
    "INVALID_PRODUCT",
    "ADDITIONAL_CONSENT_REQUIRED",
    "NO_LIABILITY_ACCOUNTS",
}
TYPES = {
    "depository": "depository",
    "credit": "credit",
    "loan": "loan",
    "investment": "investment",
    "brokerage": "investment",
}


class Plaid:
    provider = "plaid"

    def __init__(self, client_id: str, secret: str, env: str, http: httpx.Client | None = None) -> None:
        if env not in HOSTS:
            raise ValueError("PLAID_ENV must be sandbox or production")
        self.client_id = client_id
        self.secret = secret
        self.base = HOSTS[env]
        self.http = http or httpx.Client(timeout=30)

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self.http.post(self.base + path, json={"client_id": self.client_id, "secret": self.secret, **body})
        except httpx.HTTPError as e:
            raise ConnectorError(f"Couldn't reach Plaid ({type(e).__name__})", "NETWORK") from None
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            code = data.get("error_code", "") or f"HTTP_{r.status_code}"
            message = data.get("display_message") or data.get("error_message") or "Plaid returned an error"
            raise ConnectorError(message[:200], code, needs_relink=code in RELINK_CODES)
        return data

    # -- linking

    def start_link(self, user_ref: str, return_url: str, kind: str) -> LinkStart:
        products, optional = (
            (["investments"], ["transactions"])
            if kind == "investments"
            else (["transactions"], ["liabilities", "investments"])
        )
        data = self._post(
            "/link/token/create",
            {
                "client_name": "Finance",
                "language": "en",
                "country_codes": ["US"],
                "user": {"client_user_id": user_ref},
                "products": products,
                "optional_products": optional,
                "transactions": {"days_requested": 730},
                "hosted_link": {"completion_redirect_uri": return_url},
            },
        )
        url = data.get("hosted_link_url")
        if not url:
            raise ConnectorError(
                "Plaid didn't return a link page. Is Hosted Link enabled for your Plaid account?", "NO_HOSTED_LINK"
            )
        return LinkStart(url=url, pending={"link_token": data["link_token"]})

    def finish_link(self, pending: dict) -> Linked:
        data = self._post("/link/token/get", {"link_token": pending["link_token"]})
        public_token = institution = None
        for session in data.get("link_sessions") or []:
            results = session.get("results") or {}
            for item in results.get("item_add_results") or []:
                public_token = item.get("public_token")
                institution = (item.get("institution") or {}).get("name")
        if not public_token:
            raise ConnectorError("Linking wasn't finished at Plaid. Try again.", "LINK_NOT_FINISHED")
        exchanged = self._post("/item/public_token/exchange", {"public_token": public_token})
        return Linked(
            token=exchanged["access_token"], external_id=exchanged["item_id"], institution=institution or "Bank"
        )

    # -- data

    def fetch(self, token: str, external_id: str, state: dict) -> FetchResult:
        result = FetchResult(state=dict(state))
        accounts = self._post("/accounts/get", {"access_token": token})
        institution = state.get("institution") or "Bank"
        types: dict[str, str] = {}
        for a in accounts.get("accounts") or []:
            kind = TYPES.get(a.get("type", ""), "other")
            types[a["account_id"]] = kind
            b = a.get("balances") or {}
            result.accounts.append(
                AccountData(
                    external_id=a["account_id"],
                    name=a.get("name") or a.get("official_name") or "Account",
                    institution=institution,
                    type=kind,
                    subtype=a.get("subtype"),
                    mask=last4(a.get("mask")),
                    current_cents=cents(b.get("current")),
                    available_cents=cents(b.get("available")),
                )
            )

        if any(t == "investment" for t in types.values()):
            result.holdings = self._holdings(token)

        if any(t in ("depository", "credit", "loan") for t in types.values()):
            self._transactions(token, result)
        return result

    def _holdings(self, token: str) -> list[HoldingData] | None:
        try:
            data = self._post("/investments/holdings/get", {"access_token": token})
        except ConnectorError as e:
            if e.code in NOT_APPLICABLE:
                return None
            raise
        securities = {s["security_id"]: s for s in data.get("securities") or []}
        out = []
        for h in data.get("holdings") or []:
            s = securities.get(h.get("security_id"), {})
            symbol = s.get("ticker_symbol") or ("CASH" if s.get("is_cash_equivalent") else (s.get("name") or "?")[:40])
            price = Decimal(str(h.get("institution_price") or s.get("close_price") or 0))
            quantity = Decimal(str(h.get("quantity") or 0))
            value = cents(h.get("institution_value"))
            # Stock plans list unvested shares too; the account balance already leaves them out.
            vested = h.get("vested_quantity")
            if vested is not None and Decimal(str(vested)) < quantity:
                quantity = Decimal(str(vested))
                value = cents(h.get("vested_value"))
                if not quantity:
                    continue
            if value is None:
                value = int(price * quantity * 100)
            out.append(
                HoldingData(
                    account_external_id=h["account_id"],
                    symbol=symbol,
                    name=s.get("name") or symbol,
                    asset_class="Cash" if s.get("is_cash_equivalent") else asset_class_for(s.get("type"), symbol),
                    quantity=quantity,
                    price=price,
                    value_cents=value,
                    cost_basis_cents=cents(h.get("cost_basis")),
                )
            )
        return out

    def _transactions(self, token: str, result: FetchResult) -> None:
        start = result.state.get("cursor")
        for _attempt in range(3):
            cursor = start
            added: list[TransactionData] = []
            removed: list[str] = []
            try:
                while True:
                    body = {"access_token": token, "count": 500, **({"cursor": cursor} if cursor else {})}
                    data = self._post("/transactions/sync", body)
                    for t in (data.get("added") or []) + (data.get("modified") or []):
                        added.append(self._txn(t))
                    removed += [r["transaction_id"] for r in data.get("removed") or []]
                    cursor = data.get("next_cursor")
                    if not data.get("has_more"):
                        break
            except ConnectorError as e:
                if e.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                    continue  # Plaid's documented fix: start the whole pass again from the saved cursor
                if e.code in NOT_APPLICABLE or e.code == "PRODUCT_NOT_READY":
                    return
                raise
            result.transactions += added
            result.removed_transaction_ids += removed
            result.state["cursor"] = cursor
            return
        raise ConnectorError("Plaid kept changing transactions mid-sync; it'll try again next time.", "MUTATION")

    @staticmethod
    def _txn(t: dict) -> TransactionData:
        category = t.get("personal_finance_category") or {}
        return TransactionData(
            account_external_id=t["account_id"],
            external_id=t["transaction_id"],
            date=date.fromisoformat(t["date"]),
            amount_cents=-(cents(t.get("amount")) or 0),  # Plaid: positive = money out
            merchant=(t.get("merchant_name") or t.get("name") or "Unknown")[:200],
            description=(t.get("original_description") or t.get("name") or "")[:300],
            pending=bool(t.get("pending")),
            provider_category=category.get("detailed") or category.get("primary"),
        )

    def revoke(self, token: str, external_id: str) -> None:
        try:
            self._post("/item/remove", {"access_token": token})
        except ConnectorError as e:
            if e.code not in RELINK_CODES:  # already gone at Plaid is fine
                raise
