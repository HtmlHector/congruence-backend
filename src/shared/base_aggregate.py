"""Base aggregate root with domain event collection."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.shared.base_entity import BaseEntity
from src.shared.domain_event import DomainEvent


@dataclass
class BaseAggregateRoot(BaseEntity):
    """Aggregate root that collects and dispatches domain events."""

    _events: list[DomainEvent] = field(default_factory=list, init=False, repr=False)

    def record_event(self, event: DomainEvent) -> None:
        """Record an uncommitted domain event."""
        self._events.append(event)
        self.mark_updated()

    def collect_events(self) -> list[DomainEvent]:
        """Return all recorded domain events and clear the internal event buffer."""
        events = list(self._events)
        self._events.clear()
        return events
