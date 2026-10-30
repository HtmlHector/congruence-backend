"""Application settings and environment configuration.

Uses Pydantic Settings v2 with modular sub-settings for clean separation of concerns.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _DotEnvSettings(BaseSettings):
    """Base for section settings so `.env` values apply to every nested section."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AppSettings(_DotEnvSettings):
    """Core application settings."""

    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    name: str = "Parabox Backend Starter"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]
    host: str = "0.0.0.0"
    port: int = 8001
    # Public origin of this API, used to build provider OAuth callbacks. Must match
    # the callback URL registered on the provider (GitHub App, Slack, Linear).
    public_api_url: str = "http://localhost:8001"
    # Web app origin; OAuth callbacks redirect the browser back here when done.
    web_app_url: str = "http://localhost:3000"


class DatabaseSettings(_DotEnvSettings):
    """PostgreSQL database settings."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")

    url: str = "postgresql+asyncpg://parabox:parabox@localhost:5432/parabox_dev"
    pool_size: int = 20
    max_overflow: int = 10
    pool_timeout: float = 30.0
    echo: bool = False
    enable_rls: bool = True


class RedisSettings(_DotEnvSettings):
    """Redis cache and realtime adapter settings."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    url: str = "redis://localhost:6379/0"
    socket_timeout: float = 5.0
    max_connections: int = 50


class RabbitMQSettings(_DotEnvSettings):
    """RabbitMQ message bus settings."""

    model_config = SettingsConfigDict(env_prefix="RABBITMQ_", extra="ignore")

    url: str = "amqp://guest:guest@localhost:5672/"
    exchange_name: str = "parabox.events"
    prefetch_count: int = 10


class S3Settings(_DotEnvSettings):
    """S3 / MinIO object storage settings."""

    model_config = SettingsConfigDict(env_prefix="S3_", extra="ignore")

    endpoint_url: str | None = "http://localhost:9000"
    bucket_name: str = "parabox-storage"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    region: str = "us-east-1"
    presigned_url_expiration_seconds: int = 3600


class ClerkSettings(_DotEnvSettings):
    """Clerk authentication settings."""

    model_config = SettingsConfigDict(env_prefix="CLERK_", extra="ignore")

    secret_key: str = "sk_test_placeholder"
    publishable_key: str = "pk_test_placeholder"
    jwks_url: str | None = None
    webhook_secret: str = "whsec_placeholder"


class StripeSettings(_DotEnvSettings):
    """Stripe billing settings."""

    model_config = SettingsConfigDict(env_prefix="STRIPE_", extra="ignore")

    secret_key: str = "sk_test_placeholder"
    webhook_secret: str = "whsec_placeholder"
    pro_price_id: str = "price_pro_placeholder"
    enterprise_price_id: str = "price_enterprise_placeholder"


class EmailSettings(_DotEnvSettings):
    """Resend email notification settings."""

    model_config = SettingsConfigDict(env_prefix="EMAIL_", extra="ignore")

    api_key: str = "re_placeholder"
    from_address: str = "notifications@parabox.so"


class IntegrationsSettings(_DotEnvSettings):
    """Third-party OAuth and integrations credentials."""

    model_config = SettingsConfigDict(env_prefix="INTEGRATIONS_", extra="ignore")

    # GitHub App / OAuth
    github_app_id: str = "gh_app_id_placeholder"
    github_private_key_path: str = "secrets/github-app.pem"
    github_client_id: str = "gh_client_id_placeholder"
    github_client_secret: str = "gh_client_secret_placeholder"
    github_webhook_secret: str = "gh_webhook_secret_placeholder"

    # GitLab OAuth (gitlab.com)
    gitlab_client_id: str = "gitlab_client_id_placeholder"
    gitlab_client_secret: str = "gitlab_client_secret_placeholder"
    gitlab_webhook_secret: str = "gitlab_webhook_secret_placeholder"

    # Bitbucket Cloud OAuth
    bitbucket_client_id: str = "bitbucket_client_id_placeholder"
    bitbucket_client_secret: str = "bitbucket_client_secret_placeholder"

    # Slack OAuth
    slack_client_id: str = "slack_client_id_placeholder"
    slack_client_secret: str = "slack_client_secret_placeholder"

    # Linear OAuth
    linear_client_id: str = "linear_client_id_placeholder"
    linear_client_secret: str = "linear_client_secret_placeholder"

    # State signing secret
    state_signing_key: str = "parabox-oauth-signing-secret-key-123"


class DevboxSettings(_DotEnvSettings):
    """Cloud devbox lifecycle, limits, and provider selection."""

    model_config = SettingsConfigDict(env_prefix="DEVBOX_", extra="ignore")

    provider: Literal["fly", "e2b", "local", "memory"] = "local"
    image_ref: str = "ghcr.io/congruence/devbox:dev"
    docker_bin: str | None = None
    region: str = "iad"
    default_cpu: int = 2
    default_memory_mb: int = 4096
    default_disk_gb: int = 40
    boot_timeout_seconds: int = 180
    exec_timeout_seconds: int = 120
    idle_pause_seconds: int = 900
    heartbeat_seconds: int = 15
    reconciler_interval_seconds: int = 15
    capture_token_secret: str = "devbox-capture-secret-placeholder"
    max_worktrees_per_project: int = 50
    max_terminals_per_worktree: int = 8
    max_terminals_per_project: int = 32
    worktree_port_block: int = 10
    worktree_port_start: int = 30000


class FlySettings(_DotEnvSettings):
    """Fly Machines/Sprites provider credentials."""

    model_config = SettingsConfigDict(env_prefix="FLY_", extra="ignore")

    api_token: str = "placeholder"
    api_base_url: str = "https://api.machines.dev/v1"
    app_name: str = "congruence-devboxes"
    org_slug: str = "congruence"


class E2BSettings(_DotEnvSettings):
    """E2B alternate provider credentials."""

    model_config = SettingsConfigDict(env_prefix="E2B_", extra="ignore")

    api_key: str = "placeholder"
    template: str = "congruence-devbox"


class GatewaySettings(_DotEnvSettings):
    """Terminal gateway and attach-ticket configuration."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8100
    ticket_secret: str = "gateway-ticket-secret-placeholder"
    ticket_ttl_seconds: int = 60
    replay_max_bytes: int = 2_097_152
    client_buffer_max_bytes: int = 8_388_608
    max_connections_per_instance: int = 5000


class PreviewSettings(_DotEnvSettings):
    """Wildcard preview routing configuration."""

    model_config = SettingsConfigDict(env_prefix="PREVIEW_", extra="ignore")

    domain: str = "preview.congruence.dev"
    cookie_secret: str = "preview-cookie-secret-placeholder"
    cookie_ttl_seconds: int = 43200
    max_ports_per_worktree: int = 10


class LimitsSettings(_DotEnvSettings):
    """Plan entitlements for included box-hours and concurrency."""

    model_config = SettingsConfigDict(env_prefix="LIMITS_", extra="ignore")

    trial_hours: int = 20
    starter_hours: int = 50
    pro_hours: int = 250
    max_hours: int = 600
    trial_boxes: int = 2
    starter_boxes: int = 4
    pro_boxes: int = 10
    max_boxes: int = 20


class Settings(BaseSettings):
    """Aggregated root settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    encryption_key: str | None = None

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    rabbitmq: RabbitMQSettings = Field(default_factory=RabbitMQSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    clerk: ClerkSettings = Field(default_factory=ClerkSettings)
    stripe: StripeSettings = Field(default_factory=StripeSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    devbox: DevboxSettings = Field(default_factory=DevboxSettings)
    fly: FlySettings = Field(default_factory=FlySettings)
    e2b: E2BSettings = Field(default_factory=E2BSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    preview: PreviewSettings = Field(default_factory=PreviewSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton settings instance."""
    return Settings()


settings = get_settings()
