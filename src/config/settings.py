"""Application settings and typed configuration."""

from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="DATABASE_")

    url: str = Field(
        default="postgresql+asyncpg://parabox:parabox_password@localhost:5432/parabox_dev",
        validation_alias=AliasChoices("url", "DATABASE_URL", "DATABASE__URL", "POSTGRES_URL"),
    )
    pool_size: int = Field(
        default=20, validation_alias=AliasChoices("pool_size", "DATABASE_POOL_SIZE")
    )
    pool_overflow: int = Field(
        default=20, validation_alias=AliasChoices("pool_overflow", "DATABASE_POOL_OVERFLOW")
    )
    pool_timeout: int = Field(
        default=60, validation_alias=AliasChoices("pool_timeout", "DATABASE_POOL_TIMEOUT")
    )
    pool_recycle: int = Field(
        default=1800, validation_alias=AliasChoices("pool_recycle", "DATABASE_POOL_RECYCLE")
    )
    echo: bool = Field(default=False, validation_alias=AliasChoices("echo", "DATABASE_ECHO"))

    @field_validator("url", mode="after")
    @classmethod
    def normalize_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="REDIS_")

    url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("url", "REDIS_URL", "REDIS__URL"),
    )


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="AUTH_")

    supabase_url: str = Field(
        default="",
        validation_alias=AliasChoices("supabase_url", "SUPABASE_URL"),
    )
    supabase_jwt_secret: str = Field(
        default="parabox-development-jwt-secret-min-32-chars",
        validation_alias=AliasChoices("supabase_jwt_secret", "SUPABASE_JWT_SECRET"),
    )
    api_key_prefix: str = Field(
        default="pb_live_",
        validation_alias=AliasChoices("api_key_prefix", "AUTH_API_KEY_PREFIX"),
    )


class StripeSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="STRIPE_")

    secret_key: SecretStr = Field(
        default=SecretStr("sk_test_placeholder"),
        validation_alias=AliasChoices("secret_key", "STRIPE_SECRET_KEY"),
    )
    webhook_secret: SecretStr = Field(
        default=SecretStr("whsec_placeholder"),
        validation_alias=AliasChoices("webhook_secret", "STRIPE_WEBHOOK_SECRET"),
    )
    price_hobby: str = Field(
        default="price_hobby",
        validation_alias=AliasChoices("price_hobby", "STRIPE_PRICE_HOBBY"),
    )
    price_pro: str = Field(
        default="price_pro",
        validation_alias=AliasChoices("price_pro", "STRIPE_PRICE_PRO"),
    )


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="LLM_")

    provider: str = Field(
        default="openrouter",
        validation_alias=AliasChoices("provider", "LLM_PROVIDER"),
    )
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("openrouter_api_key", "OPENROUTER_API_KEY"),
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("openrouter_base_url", "OPENROUTER_BASE_URL"),
    )
    openrouter_model: str = Field(
        default="anthropic/claude-3.5-sonnet",
        validation_alias=AliasChoices("openrouter_model", "OPENROUTER_MODEL"),
    )
    openrouter_vision_model: str = Field(
        default="anthropic/claude-3.5-sonnet",
        validation_alias=AliasChoices("openrouter_vision_model", "OPENROUTER_VISION_MODEL"),
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("anthropic_api_key", "ANTHROPIC_API_KEY"),
    )
    request_timeout_seconds: float = 60.0
    enable_prompt_caching: bool = True


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="STORAGE_")

    endpoint: str = Field(
        default="localhost:9000",
        validation_alias=AliasChoices("endpoint", "STORAGE_ENDPOINT"),
    )
    bucket: str = Field(
        default="parabox-artifacts",
        validation_alias=AliasChoices("bucket", "STORAGE_BUCKET"),
    )
    access_key: str = Field(
        default="minioadmin",
        validation_alias=AliasChoices("access_key", "STORAGE_ACCESS_KEY"),
    )
    secret_key: str = Field(
        default="minioadmin",
        validation_alias=AliasChoices("secret_key", "STORAGE_SECRET_KEY"),
    )
    region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("region", "STORAGE_REGION"),
    )
    use_ssl: bool = Field(
        default=False,
        validation_alias=AliasChoices("use_ssl", "STORAGE_USE_SSL"),
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file=".env", env_file_encoding="utf-8")

    environment: str = Field(
        default="development", validation_alias=AliasChoices("environment", "ENVIRONMENT")
    )
    debug: bool = Field(default=True, validation_alias=AliasChoices("debug", "DEBUG"))
    port: int = Field(default=8000, validation_alias=AliasChoices("port", "PORT"))
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices("cors_origins", "CORS_ORIGINS"),
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    stripe: StripeSettings = Field(default_factory=StripeSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
