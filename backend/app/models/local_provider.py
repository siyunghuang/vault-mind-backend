from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from app.core.config import Settings
from app.models.base import Chunk, Message, ProviderError


class LocalProvider:
    name = "local"

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.local_model_name

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_runner: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> AsyncIterator[Chunk]:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [message.__dict__ for message in messages],
        }
        try:
            async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                response = await client.post(
                    f"{self.settings.local_model_endpoint.rstrip('/')}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"local provider unavailable: {exc}") from exc

        content = response.json().get("message", {}).get("content", "")
        yield Chunk(type="token", content=content)

    async def health(self) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(2.0, connect=0.2), trust_env=False
            ) as client:
                response = await client.get(
                    f"{self.settings.local_model_endpoint.rstrip('/')}/api/tags"
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "model": self.model}
