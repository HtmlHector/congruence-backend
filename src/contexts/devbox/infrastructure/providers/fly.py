"""Fly Machines provider (primary backend, pending the M0 Sprites spike).

Fly's Machines API is used directly because it is documented and stable. The
persistent-computer shape (`pause`/`resume` with a retained disk) maps to
stop/start here; the spec's M0 spike decides whether Sprites or Machines+volumes
is the final primary, and only this module should change as a result.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config.settings import settings
from src.contexts.devbox.infrastructure.providers.base import (
    BoxSpec,
    ProviderBox,
    ProviderError,
    ProviderNotConfigured,
)

_STATE_MAP = {
    "started": "running",
    "running": "running",
    "created": "starting",
    "starting": "starting",
    "stopped": "stopped",
    "stopping": "stopped",
    "suspended": "paused",
    "destroyed": "destroyed",
    "failed": "error",
}


class FlyProvider:
    """Creates and controls Fly Machines that host project devboxes."""

    name = "fly"

    def __init__(
        self,
        token: str | None = None,
        app_name: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._token = token or settings.fly.api_token
        self._app = app_name or settings.fly.app_name
        self._base_url = (base_url or settings.fly.api_base_url).rstrip("/")
        if not self._token or self._token == "placeholder":
            raise ProviderNotConfigured("FLY_API_TOKEN is not configured")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, object]:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise ProviderError(
                f"Fly API {method} {path} failed ({response.status_code}): {response.text[:500]}"
            )
        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def create(self, spec: BoxSpec) -> ProviderBox:
        payload = {
            "name": spec.name,
            "region": spec.region,
            "config": {
                "image": spec.image_ref,
                "guest": {
                    "cpu_kind": "shared",
                    "cpus": spec.cpu,
                    "memory_mb": spec.memory_mb,
                },
                "env": spec.env,
                "metadata": spec.metadata,
            },
        }
        machine = await self._request("POST", f"/apps/{self._app}/machines", json=payload)
        box_id = str(machine.get("id") or "")
        if not box_id:
            raise ProviderError("Fly did not return a machine id")
        return ProviderBox(box_id=box_id, provider=self.name, status="running", raw=machine)

    async def resume(self, box_id: str) -> ProviderBox:
        machine = await self._request("POST", f"/apps/{self._app}/machines/{box_id}/start", json={})
        return ProviderBox(box_id=box_id, provider=self.name, status="running", raw=machine)

    async def pause(self, box_id: str) -> None:
        await self._request("POST", f"/apps/{self._app}/machines/{box_id}/stop", json={})

    async def destroy(self, box_id: str) -> None:
        await self._request(
            "DELETE", f"/apps/{self._app}/machines/{box_id}", params={"force": "true"}
        )

    async def status(self, box_id: str) -> str:
        machine = await self._request("GET", f"/apps/{self._app}/machines/{box_id}")
        state = str(machine.get("state") or "unknown").lower()
        return _STATE_MAP.get(state, state)
