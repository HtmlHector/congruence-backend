"""Provider abstraction for creating and pausing devboxes.

The provider interface deliberately covers lifecycle only. Command execution
(PTY, git, setup) lives behind :mod:`src.contexts.devbox.infrastructure.runner`,
which in production is backed by the in-box agent, keeping this layer portable
across Fly, E2B, and local Docker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(Exception):
    """Raised when a provider operation fails."""


class ProviderNotConfigured(ProviderError):
    """Raised when the selected provider is missing credentials or support."""


@dataclass(frozen=True)
class BoxSpec:
    """Everything a provider needs to boot one devbox."""

    name: str
    image_ref: str
    region: str
    cpu: int
    memory_mb: int
    disk_gb: int
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderBox:
    """Provider-side identity and state for a devbox."""

    box_id: str
    provider: str
    status: str
    raw: dict[str, object] = field(default_factory=dict)


class DevboxProvider(Protocol):
    """Lifecycle operations every compute backend must implement."""

    name: str

    async def create(self, spec: BoxSpec) -> ProviderBox:
        """Create and boot a box."""

    async def resume(self, box_id: str) -> ProviderBox:
        """Resume a paused box."""

    async def pause(self, box_id: str) -> None:
        """Pause a box while retaining its disk."""

    async def destroy(self, box_id: str) -> None:
        """Permanently delete the box."""

    async def status(self, box_id: str) -> str:
        """Return a provider-normalized status string."""
