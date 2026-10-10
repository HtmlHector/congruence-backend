"""Application settings and environment configuration.

Uses Pydantic Settings v2 with modular sub-settings for clean separation of concerns.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Core application settings."""

    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    name: str = "Parabox Backend Starter"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    host: str = "0.0.0.0"
    port: int = 8000


class DatabaseSettings(BaseSettings):
    """PostgreSQL database settings."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")

    url: str = "postgresql+asyncpg://parabox:parabox@localhost:5432/parabox_dev"
    pool_size: int = 20
    max_overflow: int = 10
    pool_timeout: float = 30.0
    echo: bool = False
    enable_rls: bool = True


class RedisSettings(BaseSettings):
    """Redis cache and realtime adapter settings."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    url: str = "redis://localhost:6379/0"
    socket_timeout: float = 5.0
    max_connections: int = 50


class RabbitMQSettings(BaseSettings):
    """RabbitMQ message bus settings."""

    model_config = SettingsConfigDict(env_prefix="RABBITMQ_", extra="ignore")

    url: str = "amqp://guest:guest@localhost:5672/"
    exchange_name: str = "parabox.events"
    prefetch_count: int = 10


class S3Settings(BaseSettings):
    """S3 / MinIO object storage settings."""

    model_config = SettingsConfigDict(env_prefix="S3_", extra="ignore")

    endpoint_url: str | None = "http://localhost:9000"
    bucket_name: str = "parabox-storage"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    region: str = "us-east-1"
    presigned_url_expiration_seconds: int = 3600


class ClerkSettings(BaseSettings):
    """Clerk authentication settings."""

    model_config = SettingsConfigDict(env_prefix="CLERK_", extra="ignore")

    secret_key: str = "sk_test_placeholder"
    publishable_key: str = "pk_test_placeholder"
    jwks_url: str | None = None
    webhook_secret: str = "whsec_placeholder"


class StripeSettings(BaseSettings):
    """Stripe billing settings."""

    model_config = SettingsConfigDict(env_prefix="STRIPE_", extra="ignore")

    secret_key: str = "sk_test_placeholder"
    webhook_secret: str = "whsec_placeholder"
    pro_price_id: str = "price_pro_placeholder"
    enterprise_price_id: str = "price_enterprise_placeholder"


class EmailSettings(BaseSettings):
    """Resend email notification settings."""

    model_config = SettingsConfigDict(env_prefix="EMAIL_", extra="ignore")

    api_key: str = "re_placeholder"
    from_address: str = "notifications@parabox.so"


class IntegrationsSettings(BaseSettings):
    """Third-party OAuth and integrations credentials."""

    model_config = SettingsConfigDict(env_prefix="INTEGRATIONS_", extra="ignore")

    # GitHub App / OAuth
    github_client_id: str = "gh_client_id_placeholder"
    github_client_secret: str = "gh_client_secret_placeholder"
    github_webhook_secret: str = "gh_webhook_secret_placeholder"

    # Slack OAuth
    slack_client_id: str = "slack_client_id_placeholder"
    slack_client_secret: str = "slack_client_secret_placeholder"

    # Linear OAuth
    linear_client_id: str = "linear_client_id_placeholder"
    linear_client_secret: str = "linear_client_secret_placeholder"

    # State signing secret
    state_signing_key: str = "parabox-oauth-signing-secret-key-123"


class Settings(BaseSettings):
    """Aggregated root settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    rabbitmq: RabbitMQSettings = Field(default_factory=RabbitMQSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    clerk: ClerkSettings = Field(default_factory=ClerkSettings)
    stripe: StripeSettings = Field(default_factory=StripeSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton settings instance."""
    return Settings()


settings = get_settings()
