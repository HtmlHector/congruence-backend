"""Identity domain entities."""

from __future__ import annotations

from dataclasses import dataclass

from src.shared.base_aggregate import BaseAggregateRoot


@dataclass
class User(BaseAggregateRoot):
    """User account synced with Clerk authentication."""

    clerk_id: str = ""
    email: str = ""
    first_name: str | None = None
    last_name: str | None = None
    avatar_url: str | None = None
    is_active: bool = True

    @property
    def full_name(self) -> str:
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts) if parts else self.email
