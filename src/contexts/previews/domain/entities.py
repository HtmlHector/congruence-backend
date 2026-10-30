"""Domain models and value objects for the browser preview subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


PREVIEW_PURPOSE = "devbox_preview"


@dataclass
class PreviewTicketClaims:
    """Decoded claims for a preview ticket."""

    purpose: str
    project_id: str
    workspace_id: str
    user_id: str
    box_id: str
    expires_at: int
