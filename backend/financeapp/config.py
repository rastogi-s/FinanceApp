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

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
