from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://redis:6379/0"
    session_cookie_name: str = "balling_session"
    session_ttl_days: int = 30
    quota_matches_per_cycle: int = 150
    quota_warning_threshold: int = 130
    admin_login: str = ""
    admin_password: str = ""
    football_db_path: str = "/app/data/football.db"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    verification_code_ttl_minutes: int = 10
    verification_resend_cooldown_seconds: int = 60
    public_base_url: str = "https://ballingpronostics.site"
    monetbil_service_key: str = ""
    monetbil_service_secret: str = ""
    # Random, hard-to-guess path segment for the notification webhook (in
    # addition to signature verification) — Monetbil's own docs recommend
    # an unguessable URL as a first layer before the signature check.
    monetbil_webhook_path: str = ""
    monetbil_country: str = "CM"
    monetbil_currency: str = "XAF"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
