"""SnapTrade: brokerages Plaid misses or handles poorly (E*TRADE, Fidelity), free for your own accounts.

Read-only: the connection portal is opened with connectionType=read, so no trading permission is ever granted.
Works with both kinds of SnapTrade keys:
- Personal keys (the free plan for your own accounts): the key already is a SnapTrade user, so requests carry no
  userId/userSecret and nothing is registered. Stored token: "null".
- Commercial keys: the household is one registered SnapTrade user, and its user secret is the credential.
  Stored token: ["userId", "userSecret"].
Either way the token is stored encrypted like any other, and each brokerage connected through the portal becomes
accounts under the one connection.

Requests are signed as SnapTrade's SDKs do: HMAC-SHA256, keyed with the consumer key, over a canonical JSON of
{content, path, query}, sent base64-encoded in the Signature header.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from financeapp.connectors.base import (
    AccountData,
    ConnectorError,
    FetchResult,
    HoldingData,
    Linked,
    LinkStart,
    asset_class_for,
    cents,
    last4,
)

BASE = "https://api.snaptrade.com"


TYPES = {"investment": "investment", "deposit": "depository", "line_of_credit": "credit"}
# Instrument kinds, in words asset_class_for understands.
KINDS = {
    "etf": "ETF",
    "mutualfund": "Mutual fund",
    "cef": "Closed-end fund",
    "bond": "Bond",
    "crypto": "Crypto",
    "option": "Option",
    "future_option": "Option",
    "future": "Derivative",
    "cfd": "Derivative",
}


def signature(consumer_key: str, path: str, query: str, body: dict | None) -> str:
    content = json.dumps({"content": body, "path": path, "query": query}, separators=(",", ":"), sort_keys=True)
    return base64.b64encode(hmac.new(consumer_key.encode(), content.encode(), hashlib.sha256).digest()).decode()


class SnapTrade:
    provider = "snaptrade"

    def __init__(self, client_id: str, consumer_key: str, http: httpx.Client | None = None) -> None:
        self.client_id = client_id
        self.consumer_key = consumer_key
        self.http = http or httpx.Client(timeout=30)

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        query = urlencode({"clientId": self.client_id, "timestamp": str(int(time.time())), **(params or {})})
        headers = {"Signature": signature(self.consumer_key, path, query, body)}
        try:
            r = self.http.request(method, f"{BASE}{path}?{query}", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ConnectorError(f"Couldn't reach SnapTrade ({type(e).__name__})", "NETWORK") from None
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            detail = data.get("detail") or data.get("message") if isinstance(data, dict) else None
            code = str(data.get("code", "")) if isinstance(data, dict) else ""
            raise ConnectorError(
                str(detail or f"SnapTrade returned {r.status_code}")[:200],
                code or f"HTTP_{r.status_code}",
                needs_relink=r.status_code in (401, 403) or "disabled" in str(detail).lower(),
            )
        return r.json() if r.content else None

    @staticmethod
    def _user(token: str) -> dict:
        """The userId/userSecret query parameters to send: none for a Personal key."""
        user = json.loads(token)
        return {"userId": user[0], "userSecret": user[1]} if user else {}

    # -- linking

    def start_link(self, user_ref: str, return_url: str, kind: str, token: str | None = None) -> LinkStart:
        """Opens SnapTrade's connection portal. With `token` (the household's existing link) it adds a brokerage."""
        if token is not None:
            user = json.loads(token)
        else:
            try:
                reg = self._call(
                    "POST", "/snapTrade/registerUser", body={"userId": f"financeapp-{secrets.token_hex(8)}"}
                )
                user = [reg["userId"], reg["userSecret"]]
            except ConnectorError as e:
                # A Personal key is already a user and can't register more; it connects as itself.
                if "personal" not in str(e).lower():
                    raise
                user = None
        params = {"userId": user[0], "userSecret": user[1]} if user else {}
        login = self._call(
            "POST", "/snapTrade/login", params=params, body={"customRedirect": return_url, "connectionType": "read"}
        )
        url = (login or {}).get("redirectURI")
        if not url:
            raise ConnectorError("SnapTrade didn't return a connection page.", "NO_PORTAL")
        return LinkStart(url=url, pending={"user": user})

    def finish_link(self, pending: dict) -> Linked:
        token = json.dumps(pending["user"])
        user = self._user(token)
        accounts = (self._call("GET", "/accounts/all", params=user) or {}).get("results") or []
        if not accounts:
            raise ConnectorError(
                "No brokerage accounts came through. Finish connecting in the SnapTrade window.", "NO_ACCOUNTS"
            )
        names = sorted(set(self._brokerages(user).values())) or ["Brokerage"]
        return Linked(
            token=token,
            external_id=pending["user"][0] if pending["user"] else "personal",
            institution=", ".join(names)[:120],
        )

    def _brokerages(self, user: dict) -> dict[str, str]:
        """Connection id -> brokerage name, for naming accounts (accounts only carry an institution id)."""
        out = {}
        for a in self._call("GET", "/authorizations", params=user) or []:
            b = a.get("brokerage") or {}
            out[str(a.get("id"))] = b.get("display_name") or b.get("name") or a.get("name") or "Brokerage"
        return out

    # -- data

    def fetch(self, token: str, external_id: str, state: dict) -> FetchResult:
        user = self._user(token)
        positions: list[HoldingData] = []
        result = FetchResult(state=dict(state), holdings=positions)
        brokerages = self._brokerages(user)
        for a in (self._call("GET", "/accounts/all", params=user) or {}).get("results") or []:
            kind = TYPES.get(a.get("kind", ""), "other")
            result.accounts.append(
                AccountData(
                    external_id=str(a["id"]),
                    name=a.get("display_name") or "Brokerage account",
                    institution=brokerages.get(str(a.get("connection_id")), "Brokerage"),
                    type=kind,
                    subtype=a.get("raw_type") or None,
                    mask=last4(a.get("masked_account_number")),
                    current_cents=cents((a.get("net_value") or {}).get("amount")),
                )
            )
            if kind != "investment":
                continue
            listing = self._call("GET", f"/accounts/{a['id']}/positions/all", params=user) or {}
            for p in listing.get("results") or []:
                inst = p.get("instrument") or {}
                ticker = inst.get("raw_symbol") or inst.get("symbol") or "?"
                units = Decimal(str(p.get("units") or 0))
                price = Decimal(str(p.get("price") or 0))
                size = Decimal(str(inst.get("multiplier") or 1))  # option contracts: shares per contract
                basis = p.get("cost_basis")  # per share
                positions.append(
                    HoldingData(
                        account_external_id=str(a["id"]),
                        symbol=ticker[:40],
                        name=(inst.get("description") or ticker)[:200],
                        asset_class="Cash"
                        if p.get("cash_equivalent")
                        else asset_class_for(KINDS.get(inst.get("kind", ""), inst.get("kind")), ticker),
                        quantity=units,
                        price=price,
                        value_cents=int((units * price * size * 100).quantize(Decimal(1))),
                        cost_basis_cents=cents(Decimal(str(basis)) * units * size) if basis is not None else None,
                    )
                )
            for b in self._call("GET", f"/accounts/{a['id']}/balances", params=user) or []:
                cash = b.get("cash")
                if cash:
                    currency = (b.get("currency") or {}).get("code") or "USD"
                    positions.append(
                        HoldingData(
                            account_external_id=str(a["id"]),
                            symbol=f"CASH-{currency}"[:40],
                            name=f"Cash ({currency})",
                            asset_class="Cash",
                            quantity=Decimal(str(cash)),
                            price=Decimal(1),
                            value_cents=cents(cash) or 0,
                        )
                    )
        return result

    def revoke(self, token: str, external_id: str) -> None:
        user = self._user(token)
        if user:
            try:
                self._call("DELETE", "/snapTrade/deleteUser", params={"userId": user["userId"]})
            except ConnectorError as e:
                if not e.code.startswith("HTTP_404"):
                    raise
            return
        # A Personal key is your own SnapTrade account: remove each brokerage connection from it.
        for connection_id in self._brokerages(user):
            self._call("DELETE", f"/connection/{connection_id}")
