"""Settings, read once from the environment (see .env.example at the repository root)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    # postgresql+psycopg://user:password@host:5432/db. Empty means "no database configured" (unit tests).
    database_url: str = ""
    # Sample household data instead of real accounts. Used by the CI smoke test and for trying the app out.
    demo_mode: bool = False
    # Set by the image build; "dev" when running from a checkout.
    app_version: str = "dev"
    # The built Next.js export. The image puts it here; a checkout falls back to frontend/out.
    static_dir: Path = Field(default=PACKAGE_DIR.parent.parent / "frontend" / "out")
    # Host names the app answers to (anything else gets 400). Add your Tailscale name, e.g. finance.tail1234.ts.net.
    # Comma-separated.
    allowed_hosts: str = "localhost,127.0.0.1"
    # Send Strict-Transport-Security. Turn on once the app is only reached over HTTPS (tailscale serve).
    hsts: bool = False

    # The master key for tokens and API keys stored in the database: 32 random bytes, base64. Kept in .env, never
    # in the database or the backups, so a copy of either is useless on its own. update.sh makes one if missing.
    encryption_key: str = ""

    # Sign-in. Sessions end after this many idle minutes, and after max_session_hours whatever happens.
    session_idle_minutes: int = 15
    max_session_hours: int = 12
    # Wrong passwords or codes in a row before an account is locked, and for how long.
    lockout_failures: int = 5
    lockout_minutes: int = 15
    # Cookies are marked Secure (sent over HTTPS only; browsers also allow them on http://localhost).
    secure_cookies: bool = True

    # How often the background sync runs (Plaid has no webhooks here: the app is not on the internet).
    sync_interval_hours: int = 4
    sync_enabled: bool = True

    # Plaid (https://dashboard.plaid.com/developers/keys). Environment: sandbox or production.
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"
    # SnapTrade Personal (https://dashboard.snaptrade.com).
    snaptrade_client_id: str = ""
    snaptrade_consumer_key: str = ""

    # Security alerts (new-device sign-in, lockouts, broken connections). ntfy first, email as the fallback.
    ntfy_url: str = ""  # e.g. https://ntfy.sh/your-long-random-topic or your own ntfy server
    ntfy_token: str = ""
    # The address the app is opened at, e.g. https://finance.tail1234.ts.net. Tapping a notification opens the page it
    # is about there. Empty uses the first name in ALLOWED_HOSTS (https), or no link if that's only localhost.
    app_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email: str = ""

    # Live prices for holdings, every QUOTE_INTERVAL_MINUTES while US markets are open. Only ticker symbols are sent.
    # auto: Finnhub when FINNHUB_API_KEY is set (free key at https://finnhub.io), and Yahoo Finance's public chart
    # data for anything Finnhub doesn't have (mutual funds) or when there's no key. Or: finnhub | yahoo | off.
    quotes_provider: str = "auto"
    finnhub_api_key: str = ""
    quote_interval_minutes: int = 15

    # Look-through exposure reads funds' holdings from the SEC, which asks every program to say who is asking with a
    # contact email. Empty means the owner's sign-in email is used. Set LOOKTHROUGH_ENABLED=false to never ask.
    sec_contact_email: str = ""
    lookthrough_enabled: bool = True

    # Updates (see updates.py). The app asks GitHub for the latest release every few hours; UPDATE_CHECK=false never
    # asks. "Update now" leaves a request in the first folder for the server's updater; the updater reports back in
    # the second, which the app only reads. Both are mounted by docker-compose.yml.
    update_check: bool = True
    update_request_dir: Path = Path("/app/update-request")
    update_status_dir: Path = Path("/app/update-status")

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
