import asyncio
from contextlib import asynccontextmanager
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from mcp.shared.exceptions import McpError as SdkMcpError
from mcp.types import ErrorData

from app.api import chat as chat_api
from app.core.config import Settings
from app.main import app
from app.mcp import client as mcp_client
from app.models.base import Chunk, Message, ProviderError
from app.models.cloud_provider import CloudProvider
from app.models.local_provider import LocalProvider
from app.models.openai_provider import OpenAIProvider


@asynccontextmanager
async def transport(*args, **kwargs):
    yield None, None, None


def status_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    return httpx.HTTPStatusError("upstream error", request=response.request, response=response)


class TimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_normalizes_nested_failures(self):
        for cause, code, status in [
            (httpx.ReadTimeout(""), "mcp_timeout", 504),
            (TimeoutError(), "mcp_timeout", 504),
            (SdkMcpError(ErrorData(code=408, message="timeout")), "mcp_timeout", 504),
            (status_error(504), "mcp_timeout", 504),
            (httpx.ConnectError(""), "mcp_unavailable", 502),
        ]:
            for operation in ("initialize", "list_tools", "call_tool"):
                with self.subTest(cause=type(cause).__name__, operation=operation):
                    session = AsyncMock()
                    session.__aenter__.return_value = session
                    getattr(session, operation).side_effect = ExceptionGroup(
                        "transport", [ExceptionGroup("nested", [cause])]
                    )
                    with patch.object(mcp_client, "settings", Settings(mcp_server_url="http://example.test/mcp")), \
                         patch.object(mcp_client, "streamablehttp_client", transport), \
                         patch.object(mcp_client, "ClientSession", return_value=session):
                        with self.assertLogs("app.mcp.client", level="WARNING") as logs:
                            with self.assertRaises(mcp_client.McpError) as caught:
                                if operation == "call_tool":
                                    await mcp_client.call_tool("vault_read", {"path": "00-Index.md"})
                                else:
                                    await mcp_client.list_tools()
                    self.assertEqual((caught.exception.code, caught.exception.status_code), (code, status))
                    self.assertIn("elapsed_ms=", logs.output[0])
                    self.assertIn(type(cause).__name__, logs.output[0])

    async def test_mcp_deadline_and_cancellation(self):
        async def hang():
            await asyncio.Event().wait()

        for cancelled in (False, True):
            session = AsyncMock()
            session.__aenter__.return_value = session
            session.initialize.side_effect = asyncio.CancelledError() if cancelled else hang
            with patch.object(mcp_client, "settings", Settings(mcp_server_url="http://example.test/mcp")), \
                 patch.object(mcp_client, "streamablehttp_client", transport), \
                 patch.object(mcp_client, "ClientSession", return_value=session), \
                 patch.object(mcp_client, "MCP_OPERATION_TIMEOUT", 0.01):
                if cancelled:
                    with self.assertRaises(asyncio.CancelledError):
                        await mcp_client.list_tools()
                else:
                    with self.assertRaises(mcp_client.McpError) as caught:
                        await asyncio.wait_for(mcp_client.list_tools(), timeout=1)
                    self.assertEqual(caught.exception.code, "mcp_timeout")

    async def test_ai_timeouts(self):
        providers = [
            OpenAIProvider(Settings(openai_api_key="test")),
            CloudProvider(Settings(cloud_provider="nvidia", nvidia_api_key="test")),
            CloudProvider(Settings(cloud_provider="gemini", gemini_api_key="test")),
            LocalProvider(Settings()),
        ]
        for provider in providers:
            for error in (httpx.ReadTimeout(""), httpx.ConnectTimeout(""), status_error(408), status_error(504)):
                with self.subTest(provider=type(provider).__name__, error=type(error).__name__):
                    client = AsyncMock()
                    client.__aenter__.return_value = client
                    client.post.side_effect = error
                    with patch("httpx.AsyncClient", return_value=client):
                        with self.assertRaises(ProviderError) as caught:
                            async for _ in provider.generate([Message(role="user", content="hello")]):
                                pass
                    self.assertEqual((caught.exception.code, caught.exception.status_code), ("provider_timeout", 504))

    async def test_chat_errors_finish_stream_at_each_stage(self):
        class ToolProvider:
            name = "cloud"

            async def generate(self, messages, **kwargs):
                yield Chunk(type="token", content="partial")
                await kwargs["tool_runner"]("vault_read", {"path": "note.md"})
                raise AssertionError("must stop after failure")

        for stage in ("grounding", "discovery", "tool"):
            for code, status in (("mcp_timeout", 504), ("mcp_unavailable", 502)):
                with self.subTest(stage=stage, code=code):
                    failure = mcp_client.McpError("Obsidian request failed", code, status)
                    tools = AsyncMock(side_effect=failure if stage == "discovery" else None, return_value=[])
                    with patch.object(chat_api, "settings", Settings(chat_mcp_enabled=True)), \
                         patch.object(chat_api, "get_provider", return_value=ToolProvider()), \
                         patch.object(chat_api, "call_tool", side_effect=failure), \
                         patch.object(chat_api, "list_tools", tools):
                        if stage == "grounding":
                            response = await chat_api.chat(chat_api.ChatRequest(message="hello"))
                            events = [json.loads(item.removeprefix("data: ")) async for item in response.body_iterator]
                            tools.assert_not_awaited()
                        else:
                            with patch.object(chat_api, "_ground_chat_in_vault", return_value="context"):
                                response = await chat_api.chat(chat_api.ChatRequest(message="hello"))
                                events = [json.loads(item.removeprefix("data: ")) async for item in response.body_iterator]
                    self.assertEqual([event["type"] for event in events],
                                     (["token"] if stage == "tool" else []) + ["error", "done"])
                    self.assertEqual((events[-2]["code"], events[-2]["status_code"]), (code, status))

    async def test_manual_mcp_endpoints(self):
        for code, status in (("mcp_timeout", 504), ("mcp_unavailable", 502)):
            error = mcp_client.McpError("Obsidian request failed", code, status)
            with patch("app.api.mcp.list_tools", side_effect=error), \
                 patch("app.api.mcp.call_tool", side_effect=error):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    for method, path in (("GET", "/api/mcp/tools"), ("POST", "/api/mcp/tools/vault_read")):
                        response = await client.request(method, path)
                        self.assertEqual(response.status_code, status)
                        self.assertEqual(response.json()["detail"], {
                            "message": str(error), "code": code, "status_code": status,
                        })

    async def test_ai_timeout_reaches_http_chat_stream(self):
        class FailingProvider:
            name = "local"

            async def generate(self, messages, **kwargs):
                yield Chunk(type="token", content="partial")
                raise ProviderError("AI request timed out", "provider_timeout", 504)

        with patch.object(chat_api, "get_provider", return_value=FailingProvider()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/chat", json={"message": "hello"})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line]
        self.assertEqual(events, [
            {"type": "token", "content": "partial"},
            {"type": "error", "message": "AI request timed out", "code": "provider_timeout", "status_code": 504},
            {"type": "done"},
        ])
