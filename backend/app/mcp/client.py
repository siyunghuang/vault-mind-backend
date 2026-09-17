from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging
import time
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError as SdkMcpError

from app.core.config import settings


logger = logging.getLogger(__name__)
MCP_OPERATION_TIMEOUT = 30


class McpError(RuntimeError):
    def __init__(self, message: str, code: str = "mcp_unavailable", status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _causes(exc: Exception) -> list[Exception]:
    if isinstance(exc, ExceptionGroup):
        return [cause for child in exc.exceptions for cause in _causes(child)]
    return [exc]


def _is_timeout(exc: Exception) -> bool:
    return (
        isinstance(exc, (httpx.TimeoutException, TimeoutError))
        or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (408, 504)
        or isinstance(exc, SdkMcpError) and exc.error.code in (408, 504)
    )


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value


def _headers() -> dict[str, str]:
    if not settings.mcp_api_key:
        return {}
    return {"Authorization": f"Bearer {settings.mcp_api_key}"}


def _client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        verify=settings.mcp_verify_ssl,
        trust_env=False,
    )


@asynccontextmanager
async def _session(operation: str) -> AsyncIterator[ClientSession]:
    if not settings.mcp_server_url:
        raise McpError("MCP_SERVER_URL is not set")
    started = time.perf_counter()
    try:
        with anyio.fail_after(MCP_OPERATION_TIMEOUT):
            async with streamablehttp_client(
                settings.mcp_server_url,
                headers=_headers(),
                timeout=10,
                sse_read_timeout=30,
                httpx_client_factory=_client_factory,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
    except McpError:
        raise
    except Exception as exc:
        causes = _causes(exc)
        timed_out = any(_is_timeout(cause) for cause in causes)
        error = McpError(
            "Obsidian request timed out. Please try again." if timed_out
            else "Obsidian MCP request failed. Check that Obsidian and its MCP server are running.",
            code="mcp_timeout" if timed_out else "mcp_unavailable",
            status_code=504 if timed_out else 502,
        )
        logger.warning(
            "mcp_request_failed operation=%s code=%s status_code=%s elapsed_ms=%s error_types=%s",
            operation, error.code, error.status_code,
            int((time.perf_counter() - started) * 1000),
            ",".join(type(cause).__name__ for cause in causes),
        )
        raise error from exc


async def status() -> dict[str, object]:
    configured = bool(settings.mcp_server_url)
    if not configured:
        return {
            "configured": False,
            "ok": not settings.mcp_required,
            "transport": None,
            "tools": [],
        }
    try:
        tools = await list_tools()
    except Exception as exc:
        return {
            "configured": True,
            "ok": False,
            "transport": "http",
            "tools": [],
            "error": str(exc),
        }
    return {
        "configured": True,
        "ok": True,
        "transport": "http",
        "tools": [tool["name"] for tool in tools],
    }


async def list_tools() -> list[dict[str, Any]]:
    async with _session("list_tools") as session:
        result = await session.list_tools()
    return [_json(tool) for tool in result.tools]


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    async with _session(name) as session:
        result = await session.call_tool(name, arguments or {})
    return _json(result)
