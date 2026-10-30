"""In-memory devbox provider used in tests and local demos."""

from __future__ import annotations

from uuid import uuid4

from src.contexts.devbox.infrastructure.providers.base import (
    BoxSpec,
    ProviderBox,
    ProviderError,
)


class MemoryProvider:
    """A deterministic provider that tracks box state in process memory."""

    name = "memory"

    _boxes: dict[str, str] = {}

    async def create(self, _spec: BoxSpec) -> ProviderBox:
        box_id = f"mem-{uuid4().hex[:12]}"
        self._boxes[box_id] = "running"
        return ProviderBox(box_id=box_id, provider=self.name, status="running")

    async def resume(self, box_id: str) -> ProviderBox:
        if box_id not in self._boxes:
            raise ProviderError(f"Unknown memory box {box_id}")
        self._boxes[box_id] = "running"
        return ProviderBox(box_id=box_id, provider=self.name, status="running")

    async def pause(self, box_id: str) -> None:
        if box_id not in self._boxes:
            raise ProviderError(f"Unknown memory box {box_id}")
        self._boxes[box_id] = "paused"

    async def destroy(self, box_id: str) -> None:
        self._boxes.pop(box_id, None)

    async def status(self, box_id: str) -> str:
        return self._boxes.get(box_id, "unknown")
