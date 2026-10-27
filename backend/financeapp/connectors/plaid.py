"""Plaid: banks, cards and (with Investments) brokerages and retirement accounts.

Read-only products only (Transactions, Investments, Liabilities, Balance); nothing that can move money is ever
requested. Linking uses Plaid Hosted Link: the browser goes to Plaid's own page and comes back, so no Plaid script
runs inside the app and its Content-Security-Policy stays closed. The app isn't on the internet, so instead of
webhooks it asks for new transactions on every sync (/transactions/sync with a saved cursor).
"""

from datetime import date, timedelta
from decimal import Decimal

import httpx

from financeapp.connectors.base import (
    AccountData,
    CardStatementData,
    ConnectorError,
    FetchResult,
    HoldingData,
    InvestmentTransactionData,
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
INVESTMENT_HISTORY_DAYS = 730  # Plaid keeps up to 24 months of brokerage activity
INVESTMENT_OVERLAP_DAYS = 31  # look back again each time: activity can post late or be corrected
TYPES = {
    "depository": "depository",
    "credit": "credit",
    "loan": "loan",
    "investment": "investment",
    "brokerage": "investment",
}


def day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def mcc(value: str | None) -> str | None:
    """A four-digit merchant category code, or nothing (Plaid's field is in beta and not every bank sends it)."""
    code = (value or "").strip()
    return code if len(code) == 4 and code.isdigit() else None


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

    def start_link(self, user_ref: str, return_url: str, kind: str, token: str | None = None) -> LinkStart:
        """A new link, or with `token` Plaid's update mode: the person signs in again on the same Item, so a
        reconnect keeps its accounts and history and never uses up another Item."""
        body: dict = {
            "client_name": "Finance",
            "language": "en",
            "country_codes": ["US"],
            "user": {"client_user_id": user_ref},
            "hosted_link": {"completion_redirect_uri": return_url},
        }
        if token:
            body["access_token"] = token
        else:
            products, optional = (
                (["investments"], ["transactions"])
                if kind == "investments"
                else (["transactions"], ["liabilities", "investments"])
            )
            body |= {"products": products, "optional_products": optional, "transactions": {"days_requested": 730}}
        data = self._post("/link/token/create", body)
        url = data.get("hosted_link_url")
        if not url:
            raise ConnectorError(
                "Plaid didn't return a link page. Is Hosted Link enabled for your Plaid account?", "NO_HOSTED_LINK"
            )
        return LinkStart(url=url, pending={"link_token": data["link_token"], **({"update": True} if token else {})})

    def link_result(self, pending: dict) -> dict:
        """What the person linked, before anything is exchanged: the public token, the institution and the accounts
        (names and last 4 digits), so a login that's already linked can be spotted."""
        data = self._post("/link/token/get", {"link_token": pending["link_token"]})
        found: dict = {}
        for session in data.get("link_sessions") or []:
            results = session.get("results") or {}
            for item in results.get("item_add_results") or []:
                institution = item.get("institution") or {}
                found = {
                    "public_token": item.get("public_token"),
                    "institution": institution.get("name"),
                    "institution_id": institution.get("institution_id"),
                    "accounts": [
                        {
                            "name": a.get("name"),
                            "type": TYPES.get(a.get("type", ""), "other"),
                            "mask": last4(a.get("mask")),
                        }
                        for a in item.get("accounts") or []
                    ],
                }
        if not found.get("public_token"):
            raise ConnectorError("Linking wasn't finished at Plaid. Try again.", "LINK_NOT_FINISHED")
        return found

    def exchange(self, found: dict) -> Linked:
        exchanged = self._post("/item/public_token/exchange", {"public_token": found["public_token"]})
        return Linked(
            token=exchanged["access_token"],
            external_id=exchanged["item_id"],
            institution=found.get("institution") or "Bank",
        )

    def finish_link(self, pending: dict) -> Linked:
        return self.exchange(self.link_result(pending))

    def finish_update(self, pending: dict) -> None:
        """Update mode keeps the same access token; this only checks the person got all the way through."""
        data = self._post("/link/token/get", {"link_token": pending["link_token"]})
        for session in data.get("link_sessions") or []:
            results = session.get("results") or {}
            if results.get("item_add_results") or session.get("on_success"):
                return
            if session.get("finished_at") and not session.get("exit"):
                return
        raise ConnectorError("Reconnecting wasn't finished at Plaid. Try again.", "LINK_NOT_FINISHED")

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
                    limit_cents=cents(b.get("limit")) if kind == "credit" else None,
                    apy=a.get("apy") if kind == "depository" and isinstance(a.get("apy"), int | float) else None,
                )
            )

        if any(t == "investment" for t in types.values()):
            result.holdings = self._holdings(token)
            self._investment_transactions(token, result)

        if any(t in ("depository", "credit", "loan") for t in types.values()):
            # Merchant category codes came later: the first sync after that pulls the history again to get them.
            if result.state.get("cursor") and not result.state.get("mcc"):
                result.state.pop("cursor")
            self._transactions(token, result)
            result.state["mcc"] = True
        if any(t == "credit" for t in types.values()):
            result.card_statements = self._card_statements(token)
        return result

    def _card_statements(self, token: str) -> list[CardStatementData]:
        """Card terms and statements from Liabilities, only where the login already has that product: asking an Item
        without it would add it, and Plaid bills Liabilities per login. Nothing else depends on it, so a problem
        here never stops the sync."""
        try:
            item = self._post("/item/get", {"access_token": token}).get("item") or {}
            if "liabilities" not in (item.get("products") or []) + (item.get("billed_products") or []):
                return []
            data = self._post("/liabilities/get", {"access_token": token})
        except ConnectorError:
            return []
        out = []
        for c in (data.get("liabilities") or {}).get("credit") or []:
            aprs = [
                {"type": a.get("apr_type"), "percent": a.get("apr_percentage")}
                for a in c.get("aprs") or []
                if a.get("apr_percentage") is not None
            ]
            purchase = next((a["percent"] for a in aprs if a["type"] == "purchase_apr"), None)
            out.append(
                CardStatementData(
                    account_external_id=c["account_id"],
                    purchase_apr=Decimal(str(purchase)) if purchase is not None else None,
                    aprs=aprs,
                    statement_cents=cents(c.get("last_statement_balance")),
                    statement_date=day(c.get("last_statement_issue_date")),
                    minimum_cents=cents(c.get("minimum_payment_amount")),
                    due_date=day(c.get("next_payment_due_date")),
                    last_payment_cents=cents(c.get("last_payment_amount")),
                    last_payment_date=day(c.get("last_payment_date")),
                    overdue=c.get("is_overdue"),
                )
            )
        return out

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
            basis = cents(h.get("cost_basis"))
            unvested = None
            # Stock plans list unvested shares too; the account balance already leaves them out. Their cost basis
            # covers every share, vested or not, so the vested shares get their share of it.
            vested = h.get("vested_quantity")
            if vested is not None and Decimal(str(vested)) < quantity:
                total = quantity
                quantity = Decimal(str(vested))
                unvested = total - quantity
                value = cents(h.get("vested_value")) if quantity else 0
                if basis is not None:
                    basis = int((Decimal(basis) * quantity / total).to_integral_value()) if total else None
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
                    cost_basis_cents=basis,
                    unvested_quantity=unvested,
                )
            )
        return out

    def _investment_transactions(self, token: str, result: FetchResult, today: date | None = None) -> None:
        """Dividends, interest, buys and sells: the full 24 months the first time, then the last month again."""
        end = today or date.today()
        through = result.state.get("investments_through")
        start = (
            date.fromisoformat(through) - timedelta(days=INVESTMENT_OVERLAP_DAYS)
            if through
            else end - timedelta(days=INVESTMENT_HISTORY_DAYS)
        )
        found: list[InvestmentTransactionData] = []
        offset = 0
        try:
            while True:
                data = self._post(
                    "/investments/transactions/get",
                    {
                        "access_token": token,
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "options": {"count": 500, "offset": offset},
                    },
                )
                securities = {s["security_id"]: s for s in data.get("securities") or []}
                page = data.get("investment_transactions") or []
                for t in page:
                    s = securities.get(t.get("security_id") or "", {})
                    quantity, price = t.get("quantity"), t.get("price")
                    found.append(
                        InvestmentTransactionData(
                            account_external_id=t["account_id"],
                            external_id=t["investment_transaction_id"],
                            date=date.fromisoformat(t["date"]),
                            type=(t.get("type") or "")[:24],
                            subtype=(t.get("subtype") or "")[:48],
                            symbol=(s.get("ticker_symbol") or "")[:40] or None,
                            name=(t.get("name") or s.get("name") or "")[:200],
                            quantity=Decimal(str(quantity)) if quantity is not None else None,
                            price=Decimal(str(price)) if price is not None else None,
                            amount_cents=-(cents(t.get("amount")) or 0),  # Plaid: positive = cash out
                        )
                    )
                offset += len(page)
                if not page or offset >= (data.get("total_investment_transactions") or 0):
                    break
        except ConnectorError as e:
            if e.code in NOT_APPLICABLE or e.code == "PRODUCT_NOT_READY":
                return  # no brokerage activity here, or Plaid is still gathering it: next sync asks again
            raise
        result.investment_transactions += found
        result.state["investments_through"] = end.isoformat()

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
            mcc=mcc(t.get("merchant_category_code")),
            channel=t.get("payment_channel") if t.get("payment_channel") in ("online", "in store", "other") else None,
            website=(t.get("website") or "")[:120] or None,
        )

    def revoke(self, token: str, external_id: str) -> None:
        try:
            self._post("/item/remove", {"access_token": token})
        except ConnectorError as e:
            if e.code not in RELINK_CODES:  # already gone at Plaid is fine
                raise
