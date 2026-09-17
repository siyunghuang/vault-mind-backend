from collections.abc import AsyncIterator, Awaitable, Callable
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.diagnostics import log_usage, record_ai_request
from app.models.base import Chunk, Message, ProviderError

logger = logging.getLogger(__name__)

class LocalProvider:
    name = "local"

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.local_model_name

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_runner: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> AsyncIterator[Chunk]:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [message.__dict__ for message in messages],
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                record_ai_request()
                response = await client.post(
                    f"{self.settings.local_model_endpoint.rstrip('/')}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            timed_out = isinstance(exc, httpx.TimeoutException) or (
                isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (408, 504)
            )
            code = "provider_timeout" if timed_out else "provider_http_error"
            status_code = 504 if timed_out else 502
            logger.warning(
                "local_provider_request_failed provider=local model=%s code=%s status_code=%s elapsed_ms=%s error=%s",
                self.model, code, status_code,
                int((time.perf_counter() - started) * 1000), type(exc).__name__,
            )
            raise ProviderError(
                "Local AI request timed out. Please try again." if timed_out
                else "Local AI request failed. Check that the model server is running.",
                code=code, status_code=status_code,
            ) from exc

        data = response.json()
        log_usage("local", self.model, {
            "input_tokens": data.get("prompt_eval_count", "unavailable"),
            "output_tokens": data.get("eval_count", "unavailable"),
        })
        content = data.get("message", {}).get("content", "")
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
