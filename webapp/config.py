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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
