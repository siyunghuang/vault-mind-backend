from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.core.config import settings


class McpError(RuntimeError):
    pass


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
async def _session() -> AsyncIterator[ClientSession]:
    if not settings.mcp_server_url:
        raise McpError("MCP_SERVER_URL is not set")
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
    async with _session() as session:
        result = await session.list_tools()
    return [_json(tool) for tool in result.tools]


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    async with _session() as session:
        result = await session.call_tool(name, arguments or {})
    return _json(result)
