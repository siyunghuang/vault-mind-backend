from collections.abc import AsyncIterator, Awaitable, Callable
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.models.base import Chunk, Message, ProviderError

logger = logging.getLogger(__name__)
SECRET_KEYS = ("api_key", "authorization", "bearer", "password", "secret", "token")


def _redact(value: Any, limit: int) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if any(secret in key.lower() for secret in SECRET_KEYS) else _redact(item, limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, limit) for item in value]
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit]}...<truncated>"
    return value


def _preview(value: Any, limit: int) -> str:
    return json.dumps(_redact(value, limit), default=str)[:limit]


def _json_len(value: Any) -> int:
    return len(json.dumps(value, default=str))


def _http_error_code(status_code: int) -> str:
    return {
        400: "provider_bad_request",
        413: "provider_payload_too_large",
        429: "rate_limited",
    }.get(status_code, "provider_http_status")


class CloudProvider:
    name = "cloud"

    def __init__(self, settings: Settings, cloud_provider: str | None = None, model: str | None = None) -> None:
        self.settings = settings
        self.cloud_provider = cloud_provider or settings.cloud_provider
        self.model = model

    def _config(self) -> tuple[str, str, str]:
        if self.cloud_provider == "gemini":
            return (
                self.settings.gemini_api_key,
                self.settings.gemini_base_url,
                self.model or self.settings.gemini_model,
            )
        return (
            self.settings.nvidia_api_key,
            self.settings.nvidia_base_url,
            self.model or self.settings.cloud_model,
        )

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_runner: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> AsyncIterator[Chunk]:
        api_key, base_url, model = self._config()
        key_name = "GEMINI_API_KEY" if self.cloud_provider == "gemini" else "NVIDIA_API_KEY"
        if not api_key:
            raise ProviderError(
                f"cloud provider unavailable: {key_name} is not set",
                code="missing_api_key",
            )

        payload_messages: list[dict[str, Any]] = [message.__dict__ for message in messages]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
                for round_index in range(self.settings.chat_mcp_max_rounds):
                    payload: dict[str, Any] = {
                        "model": model,
                        "messages": payload_messages,
                        "max_tokens": 8192,
                        "temperature": 1.0,
                        "top_p": 0.95,
                        "stream": False,
                    }
                    if tools and tool_runner:
                        payload["tools"] = tools
                        payload["tool_choice"] = "auto"
                    log_extra = ""
                    if self.settings.log_payloads:
                        log_extra = f" payload_preview={_preview(payload, self.settings.log_payload_chars)}"
                    logger.info(
                        "cloud_provider_request provider=%s model=%s round=%s messages=%s tools=%s request_bytes=%s%s",
                        self.cloud_provider,
                        model,
                        round_index + 1,
                        len(payload_messages),
                        len(tools or []),
                        _json_len(payload),
                        log_extra,
                    )
                    started = time.perf_counter()
                    response = await client.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    response.raise_for_status()
                    message = response.json()["choices"][0]["message"]
                    tool_calls = message.get("tool_calls") or []
                    response_extra = ""
                    if self.settings.log_payloads:
                        response_extra = f" response_preview={_preview(message, self.settings.log_payload_chars)}"
                    logger.info(
                        "cloud_provider_response provider=%s model=%s round=%s status_code=%s elapsed_ms=%s tool_calls=%s content_chars=%s%s",
                        self.cloud_provider,
                        model,
                        round_index + 1,
                        response.status_code,
                        elapsed_ms,
                        len(tool_calls),
                        len(message.get("content") or ""),
                        response_extra,
                    )
                    if not tool_calls or not tool_runner:
                        yield Chunk(type="token", content=message.get("content") or "")
                        return

                    payload_messages.append(message)
                    for tool_call in tool_calls:
                        function = tool_call["function"]
                        name = function["name"]
                        arguments = json.loads(function.get("arguments") or "{}")
                        args_extra = ""
                        if self.settings.log_payloads:
                            args_extra = f" args_preview={_preview(arguments, self.settings.log_payload_chars)}"
                        logger.info(
                            "cloud_provider_tool_call_requested provider=%s model=%s round=%s tool=%s tool_call_id=%s args_bytes=%s%s",
                            self.cloud_provider,
                            model,
                            round_index + 1,
                            name,
                            tool_call["id"],
                            _json_len(arguments),
                            args_extra,
                        )
                        yield Chunk(type="tool_call", content=name)
                        result = await tool_runner(name, arguments)
                        yield Chunk(type="tool_result", content=name)
                        payload_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "name": name,
                                "content": json.dumps(result),
                            }
                        )
                logger.warning(
                    "cloud_provider_tool_round_limit_finalize provider=%s model=%s rounds=%s messages=%s",
                    self.cloud_provider,
                    model,
                    self.settings.chat_mcp_max_rounds,
                    len(payload_messages),
                )
                payload_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool budget reached. Do not call tools. Answer now using the tool "
                            "results already available. If incomplete, say what is missing."
                        ),
                    }
                )
                payload = {
                    "model": model,
                    "messages": payload_messages,
                    "max_tokens": 8192,
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "stream": False,
                }
                log_extra = ""
                if self.settings.log_payloads:
                    log_extra = f" payload_preview={_preview(payload, self.settings.log_payload_chars)}"
                logger.info(
                    "cloud_provider_request provider=%s model=%s round=%s messages=%s tools=%s request_bytes=%s%s",
                    self.cloud_provider,
                    model,
                    self.settings.chat_mcp_max_rounds + 1,
                    len(payload_messages),
                    0,
                    _json_len(payload),
                    log_extra,
                )
                started = time.perf_counter()
                response = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                response.raise_for_status()
                message = response.json()["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []
                response_extra = ""
                if self.settings.log_payloads:
                    response_extra = f" response_preview={_preview(message, self.settings.log_payload_chars)}"
                logger.info(
                    "cloud_provider_response provider=%s model=%s round=%s status_code=%s elapsed_ms=%s tool_calls=%s content_chars=%s%s",
                    self.cloud_provider,
                    model,
                    self.settings.chat_mcp_max_rounds + 1,
                    response.status_code,
                    elapsed_ms,
                    len(tool_calls),
                    len(message.get("content") or ""),
                    response_extra,
                )
                if not tool_calls and message.get("content"):
                    yield Chunk(type="token", content=message["content"])
                    return
                raise ProviderError(
                    "cloud provider exceeded MCP tool round limit",
                    code="tool_round_limit",
                    status_code=508,
                )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            code = _http_error_code(status_code)
            body_preview = _preview(exc.response.text, self.settings.log_payload_chars)
            logger.warning(
                "cloud_provider_http_error provider=%s model=%s status_code=%s code=%s url=%s response_preview=%s",
                self.cloud_provider,
                model,
                status_code,
                code,
                exc.request.url,
                body_preview,
            )
            raise ProviderError(
                f"cloud provider unavailable: HTTP {status_code}",
                code=code,
                status_code=status_code,
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "cloud_provider_timeout provider=%s model=%s error=%s",
                self.cloud_provider,
                model,
                type(exc).__name__,
            )
            raise ProviderError(
                f"cloud provider timed out: {type(exc).__name__}",
                code="provider_timeout",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            logger.warning(
                "cloud_provider_http_error provider=%s model=%s error=%s",
                self.cloud_provider,
                model,
                detail,
            )
            raise ProviderError(
                f"cloud provider unavailable: {detail}",
                code="provider_http_error",
                status_code=502,
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            detail = str(exc) or type(exc).__name__
            logger.warning(
                "cloud_provider_bad_response provider=%s model=%s error=%s",
                self.cloud_provider,
                model,
                detail,
            )
            raise ProviderError(
                f"cloud provider returned an unexpected response: {detail}",
                code="provider_bad_response",
                status_code=502,
            ) from exc

    async def health(self) -> dict[str, object]:
        api_key, base_url, model = self._config()
        return {
            "ok": bool(api_key),
            "provider": self.cloud_provider,
            "model": model,
            "base_url": base_url,
        }
