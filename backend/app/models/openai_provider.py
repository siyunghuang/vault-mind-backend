from collections.abc import AsyncIterator, Awaitable, Callable
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.diagnostics import log_usage, record_ai_request
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
        408: "provider_timeout",
        413: "provider_payload_too_large",
        429: "rate_limited",
        504: "provider_timeout",
    }.get(status_code, "provider_http_status")


def _messages_to_input(messages: list[Message]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _response_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    response_tools = []
    for tool in tools or []:
        function = tool.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        response_tools.append(
            {
                "type": "function",
                "name": name,
                "description": function.get("description") or name,
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return response_tools


def _output_text(response: dict[str, Any]) -> str:
    text = response.get("output_text")
    if isinstance(text, str):
        return text
    chunks = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                chunks.append(str(content.get("text") or ""))
    return "".join(chunks)


def _function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in response.get("output") or []
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]


class OpenAIProvider:
    name = "cloud"

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.cloud_provider = "openai"
        self.model = model

    def _config(self) -> tuple[str, str, str]:
        return (
            self.settings.openai_api_key,
            self.settings.openai_base_url,
            self.model or self.settings.openai_model,
        )

    async def _post(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        model: str,
        round_index: int,
    ) -> dict[str, Any]:
        log_extra = ""
        if self.settings.log_payloads:
            log_extra = f" payload_preview={_preview(payload, self.settings.log_payload_chars)}"
        logger.info(
            "cloud_provider_request provider=%s model=%s round=%s messages=%s tools=%s request_bytes=%s%s",
            self.cloud_provider,
            model,
            round_index,
            len(payload.get("input") or []),
            len(payload.get("tools") or []),
            _json_len(payload),
            log_extra,
        )
        started = time.perf_counter()
        record_ai_request()
        response = await client.post(
            f"{base_url.rstrip('/')}/responses",
            headers=headers,
            json=payload,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        data = response.json()
        log_usage(self.cloud_provider, model, data.get("usage"))
        tool_calls = _function_calls(data)
        content = _output_text(data)
        response_extra = ""
        if self.settings.log_payloads:
            response_extra = f" response_preview={_preview(data, self.settings.log_payload_chars)}"
        logger.info(
            "cloud_provider_response provider=%s model=%s round=%s status_code=%s elapsed_ms=%s tool_calls=%s content_chars=%s%s",
            self.cloud_provider,
            model,
            round_index,
            response.status_code,
            elapsed_ms,
            len(tool_calls),
            len(content),
            response_extra,
        )
        return data

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_runner: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> AsyncIterator[Chunk]:
        api_key, base_url, model = self._config()
        if not api_key:
            raise ProviderError("cloud provider unavailable: OPENAI_API_KEY is not set", code="missing_api_key")

        input_items: list[dict[str, Any]] = _messages_to_input(messages)
        response_tools = _response_tools(tools)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        request_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
                for round_index in range(self.settings.chat_mcp_max_rounds):
                    payload: dict[str, Any] = {
                        "model": model,
                        "input": input_items,
                        "max_output_tokens": 8192,
                    }
                    if response_tools and tool_runner:
                        payload["tools"] = response_tools
                        payload["tool_choice"] = "auto"
                    response = await self._post(client, base_url, headers, payload, model, round_index + 1)
                    tool_calls = _function_calls(response)
                    if not tool_calls or not tool_runner:
                        yield Chunk(type="token", content=_output_text(response))
                        return

                    input_items.extend(response.get("output") or [])
                    for tool_call in tool_calls:
                        name = str(tool_call["name"])
                        arguments = json.loads(tool_call.get("arguments") or "{}")
                        args_extra = ""
                        if self.settings.log_payloads:
                            args_extra = f" args_preview={_preview(arguments, self.settings.log_payload_chars)}"
                        logger.info(
                            "cloud_provider_tool_call_requested provider=%s model=%s round=%s tool=%s tool_call_id=%s args_bytes=%s%s",
                            self.cloud_provider,
                            model,
                            round_index + 1,
                            name,
                            tool_call.get("call_id") or tool_call.get("id"),
                            _json_len(arguments),
                            args_extra,
                        )
                        yield Chunk(type="tool_call", content=name)
                        result = await tool_runner(name, arguments)
                        yield Chunk(type="tool_result", content=name)
                        input_items.append(
                            {
                                "type": "function_call_output",
                                "call_id": tool_call["call_id"],
                                "output": json.dumps(result),
                            }
                        )

                logger.warning(
                    "cloud_provider_tool_round_limit_finalize provider=%s model=%s rounds=%s messages=%s",
                    self.cloud_provider,
                    model,
                    self.settings.chat_mcp_max_rounds,
                    len(input_items),
                )
                input_items.append(
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
                    "input": input_items,
                    "max_output_tokens": 8192,
                }
                response = await self._post(
                    client,
                    base_url,
                    headers,
                    payload,
                    model,
                    self.settings.chat_mcp_max_rounds + 1,
                )
                if not _function_calls(response) and _output_text(response):
                    yield Chunk(type="token", content=_output_text(response))
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
                "cloud_provider_http_error provider=%s model=%s status_code=%s code=%s url=%s response_preview=%s elapsed_ms=%s error=%s",
                self.cloud_provider,
                model,
                status_code,
                code,
                exc.request.url,
                body_preview,
                int((time.perf_counter() - request_started) * 1000),
                type(exc).__name__,
            )
            raise ProviderError(
                f"cloud provider unavailable: HTTP {status_code}",
                code=code,
                status_code=504 if code == "provider_timeout" else status_code,
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "cloud_provider_timeout provider=%s model=%s error=%s code=provider_timeout status_code=504 elapsed_ms=%s",
                self.cloud_provider,
                model,
                type(exc).__name__,
                int((time.perf_counter() - request_started) * 1000),
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
