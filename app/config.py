from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-before-deploying"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    database_url: str = "postgresql+asyncpg://linkly:linkly@localhost:5432/linkly"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24

    base_url: str = "http://localhost:8000"

    create_limit_per_hour: int = 30
    redirect_cache_ttl_seconds: int = 3600

    # Salt for hashing visitor IPs. Raw IPs are never stored.
    ip_hash_salt: str = "linkly-default-salt"

    @model_validator(mode="after")
    def _reject_weak_secret_in_production(self) -> "Settings":
        """Fail at startup, not at the first forged token."""
        if self.environment == "production" and (
            self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError(
                "JWT_SECRET must be set to a unique value of at least 32 characters "
                "when ENVIRONMENT=production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
