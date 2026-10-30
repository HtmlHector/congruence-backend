"""Provider registry for devbox lifecycle backends."""

from __future__ import annotations

from src.config.settings import settings
from src.contexts.devbox.infrastructure.providers.base import (
    BoxSpec,
    DevboxProvider,
    ProviderBox,
    ProviderError,
    ProviderNotConfigured,
)
from src.contexts.devbox.infrastructure.providers.fly import FlyProvider
from src.contexts.devbox.infrastructure.providers.local import LocalDockerProvider
from src.contexts.devbox.infrastructure.providers.memory import MemoryProvider

__all__ = [
    "BoxSpec",
    "DevboxProvider",
    "FlyProvider",
    "LocalDockerProvider",
    "MemoryProvider",
    "ProviderBox",
    "ProviderError",
    "ProviderNotConfigured",
    "get_provider",
]


def get_provider(name: str | None = None) -> DevboxProvider:
    """Build the configured provider, forcing the in-memory backend in tests."""
    resolved = name or settings.devbox.provider
    if settings.app.environment == "test":
        resolved = "memory"

    if resolved == "memory":
        return MemoryProvider()
    if resolved == "local":
        return LocalDockerProvider()
    if resolved == "fly":
        return FlyProvider()
    if resolved == "e2b":
        raise ProviderNotConfigured(
            "The E2B provider is an alternate backend and is not wired yet; "
            "use DEVBOX_PROVIDER=fly or local."
        )
    raise ProviderNotConfigured(f"Unknown devbox provider '{resolved}'")
