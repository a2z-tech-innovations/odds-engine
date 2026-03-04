from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Odds API
    odds_api_key: str
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    # Database (individual components assembled into URL)
    basestar_address: str
    db_name: str
    db_user: str
    db_password: str
    db_port: int = 5432

    # Redis (individual components assembled into URL)
    cache_user: str = ""
    cache_password: str
    cache_port: int = 6379
    cache_db: int = 0

    # Auth
    api_secret_key: str = "dev-secret-change-in-prod"

    # Budget
    monthly_credit_limit: int = 500
    daily_credit_target: int = 16

    # Scheduler
    scheduler_enabled: bool = True
    pre_match_interval_minutes: int = 60
    live_interval_minutes: int = 10
    sports_discovery_interval_minutes: int = 60

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.basestar_address}:{self.db_port}/{self.db_name}"
        )

    @computed_field
    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic migrations."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.basestar_address}:{self.db_port}/{self.db_name}"
        )

    @computed_field
    @property
    def redis_url(self) -> str:
        if self.cache_password:
            return (
                f"redis://{self.cache_user}:{self.cache_password}"
                f"@{self.basestar_address}:{self.cache_port}/{self.cache_db}"
            )
        return f"redis://{self.basestar_address}:{self.cache_port}/{self.cache_db}"


def get_settings() -> Settings:
    return Settings()
