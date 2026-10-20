"""SnapTrade Personal: brokerages Plaid misses or handles poorly (E*TRADE, Fidelity), free for your own accounts.

Read-only: the connection portal is opened with connectionType=read, so no trading permission is ever granted.
The household is one SnapTrade user; each brokerage you connect through the portal becomes accounts under it.
The user secret SnapTrade hands back is the credential, and is stored encrypted like any other token.

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
PREFIX = "/api/v1"


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
        full = PREFIX + path
        headers = {"Signature": signature(self.consumer_key, full, query, body)}
        try:
            r = self.http.request(method, f"{BASE}{full}?{query}", json=body, headers=headers)
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
        user_id, user_secret = json.loads(token)
        return {"userId": user_id, "userSecret": user_secret}

    # -- linking

    def start_link(self, user_ref: str, return_url: str, kind: str, token: str | None = None) -> LinkStart:
        """Opens SnapTrade's connection portal. With `token` (an existing household user) it adds a brokerage to it."""
        if token:
            user = self._user(token)
        else:
            reg = self._call("POST", "/snapTrade/registerUser", body={"userId": f"financeapp-{secrets.token_hex(8)}"})
            user = {"userId": reg["userId"], "userSecret": reg["userSecret"]}
        login = self._call(
            "POST", "/snapTrade/login", params=user, body={"customRedirect": return_url, "connectionType": "read"}
        )
        url = (login or {}).get("redirectURI")
        if not url:
            raise ConnectorError("SnapTrade didn't return a connection page.", "NO_PORTAL")
        return LinkStart(url=url, pending={"user": [user["userId"], user["userSecret"]]})

    def finish_link(self, pending: dict) -> Linked:
        token = json.dumps(pending["user"])
        accounts = self._call("GET", "/accounts", params=self._user(token)) or []
        if not accounts:
            raise ConnectorError(
                "No brokerage accounts came through. Finish connecting in the SnapTrade window.", "NO_ACCOUNTS"
            )
        names = sorted({a.get("institution_name") or "Brokerage" for a in accounts})
        return Linked(token=token, external_id=pending["user"][0], institution=", ".join(names)[:120])

    # -- data

    def fetch(self, token: str, external_id: str, state: dict) -> FetchResult:
        user = self._user(token)
        positions: list[HoldingData] = []
        result = FetchResult(state=dict(state), holdings=positions)
        for a in self._call("GET", "/accounts", params=user) or []:
            total = ((a.get("balance") or {}).get("total") or {}).get("amount")
            result.accounts.append(
                AccountData(
                    external_id=str(a["id"]),
                    name=a.get("name") or "Brokerage account",
                    institution=a.get("institution_name") or "Brokerage",
                    type="investment",
                    subtype=((a.get("meta") or {}).get("type") or None),
                    mask=last4(a.get("number")),
                    current_cents=cents(total),
                )
            )
            holdings = self._call("GET", f"/accounts/{a['id']}/holdings", params=user) or {}
            for p in holdings.get("positions") or []:
                inner = (p.get("symbol") or {}).get("symbol") or {}
                ticker = inner.get("symbol") or inner.get("raw_symbol") or "?"
                units = Decimal(str(p.get("units") or 0))
                price = Decimal(str(p.get("price") or 0))
                avg = p.get("average_purchase_price")
                positions.append(
                    HoldingData(
                        account_external_id=str(a["id"]),
                        symbol=ticker[:40],
                        name=(inner.get("description") or ticker)[:200],
                        asset_class=asset_class_for((inner.get("type") or {}).get("description"), ticker),
                        quantity=units,
                        price=price,
                        value_cents=int((units * price * 100).quantize(Decimal(1))),
                        cost_basis_cents=cents(Decimal(str(avg)) * units) if avg is not None else None,
                    )
                )
            for b in holdings.get("balances") or []:
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
        try:
            self._call("DELETE", "/snapTrade/deleteUser", params={"userId": user["userId"]})
        except ConnectorError as e:
            if not e.code.startswith("HTTP_404"):
                raise
