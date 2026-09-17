import unittest
import asyncio
import json
import logging

from app.api.models import models
from app.core.config import Settings
from app.core.sse import event, stream_error
from app.main import app
from app.models.base import Chunk, Message, ProviderError
from app.models.cloud_provider import CloudProvider
from app.models.factory import get_provider
from app.models.local_provider import LocalProvider
from app.models.openai_provider import OpenAIProvider
from app.schemas.chat import ChatRequest
import app.api.chat as chat_api
import app.models.cloud_provider as cloud_provider
import app.models.openai_provider as openai_provider
from app.mcp import client as mcp_client


class BackendTest(unittest.TestCase):
    def test_event_formats_sse_json(self) -> None:
        self.assertEqual(event({"type": "done"}), 'data: {"type":"done"}\n\n')

    def test_logging_format_includes_timestamp(self) -> None:
        formatter = logging.getLogger().handlers[0].formatter

        self.assertIn("%(asctime)s", formatter._fmt)

    def test_stream_error_includes_code_and_status(self) -> None:
        items = asyncio.run(_collect(stream_error("slow down", code="rate_limited", status_code=429)))

        self.assertEqual(
            items[0],
            'data: {"type":"error","message":"slow down","code":"rate_limited","status_code":429}\n\n',
        )
        self.assertEqual(items[1], 'data: {"type":"done"}\n\n')

    def test_payload_preview_redacts_secret_values(self) -> None:
        preview = chat_api._preview({"api_key": "secret-value", "content": "abcdef"}, 80)

        self.assertIn("[redacted]", preview)
        self.assertNotIn("secret-value", preview)

    def test_models_endpoint(self) -> None:
        response = asyncio.run(models())
        self.assertEqual(response["default"], "local")
        self.assertEqual(response["default_model_id"], "local:llama3")
        self.assertIn("local:llama3", [model["id"] for model in response["models"]])
        self.assertIn("openai:gpt-5.4-mini", [model["id"] for model in response["models"]])
        self.assertLessEqual(
            {model["provider"] for model in response["models"]},
            {"local", "nvidia", "gemini", "openai"},
        )
        self.assertEqual(app.title, "Vault Mind Backend")

    def test_model_id_selects_cloud_model(self) -> None:
        provider = get_provider(model_id="gemini:gemini-3.5-flash")

        self.assertIsInstance(provider, CloudProvider)
        self.assertEqual(provider.cloud_provider, "gemini")
        self.assertEqual(provider.model, "gemini-3.5-flash")

    def test_model_id_selects_openai_model(self) -> None:
        provider = get_provider(model_id="openai:gpt-5.4-mini")

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model, "gpt-5.4-mini")

    def test_model_id_selects_local_model(self) -> None:
        provider = get_provider(model_id="local:llama3")

        self.assertIsInstance(provider, LocalProvider)
        self.assertEqual(provider.model, "llama3")

    def test_unknown_model_id_is_rejected(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            get_provider(model_id="gemini:not-allowed")

        self.assertEqual(caught.exception.code, "model_not_allowed")
        self.assertEqual(caught.exception.status_code, 400)

    def test_cloud_provider_requires_nvidia_key(self) -> None:
        async def run() -> None:
            provider = CloudProvider(Settings(cloud_provider="nvidia", nvidia_api_key=""))
            with self.assertRaisesRegex(ProviderError, "NVIDIA_API_KEY"):
                async for _ in provider.generate([Message(role="user", content="hello")]):
                    pass

        asyncio.run(run())

    def test_cloud_provider_requires_gemini_key(self) -> None:
        async def run() -> None:
            provider = CloudProvider(Settings(cloud_provider="gemini", gemini_api_key=""))
            with self.assertRaisesRegex(ProviderError, "GEMINI_API_KEY"):
                async for _ in provider.generate([Message(role="user", content="hello")]):
                    pass

        asyncio.run(run())

    def test_openai_provider_requires_key(self) -> None:
        async def run() -> None:
            provider = OpenAIProvider(Settings(openai_api_key=""))
            with self.assertRaisesRegex(ProviderError, "OPENAI_API_KEY"):
                async for _ in provider.generate([Message(role="user", content="hello")]):
                    pass

        asyncio.run(run())

    def test_cloud_provider_parses_nvidia_response(self) -> None:
        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "hello"}}]}

        class FakeClient:
            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, url: str, **kwargs: object) -> FakeResponse:
                self.url = url
                self.kwargs = kwargs
                return FakeResponse()

        fake = FakeClient()
        old_client = cloud_provider.httpx.AsyncClient
        cloud_provider.httpx.AsyncClient = lambda **_: fake
        try:
            provider = CloudProvider(Settings(cloud_provider="nvidia", nvidia_api_key="key"))
            with self.assertLogs("app.models.cloud_provider", level="INFO") as logs:
                chunks = asyncio.run(_collect(provider.generate([Message(role="user", content="hi")])))
        finally:
            cloud_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunks[0].content, "hello")
        self.assertEqual(fake.url, "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(fake.kwargs["json"]["model"], "minimaxai/minimax-m3")
        output = "\n".join(logs.output)
        self.assertIn("cloud_provider_request", output)
        self.assertIn("cloud_provider_response", output)
        self.assertNotIn("content=hi", output)

    def test_cloud_provider_parses_gemini_response(self) -> None:
        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "hello"}}]}

        class FakeClient:
            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, url: str, **kwargs: object) -> FakeResponse:
                self.url = url
                self.kwargs = kwargs
                return FakeResponse()

        fake = FakeClient()
        old_client = cloud_provider.httpx.AsyncClient
        cloud_provider.httpx.AsyncClient = lambda **_: fake
        try:
            provider = CloudProvider(Settings(cloud_provider="gemini", gemini_api_key="key"))
            chunks = asyncio.run(_collect(provider.generate([Message(role="user", content="hi")])))
        finally:
            cloud_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunks[0].content, "hello")
        self.assertEqual(
            fake.url,
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )
        self.assertEqual(fake.kwargs["json"]["model"], "gemini-3.5-flash")

    def test_openai_provider_parses_response_output_text(self) -> None:
        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {"output_text": "hello", "output": []}

        class FakeClient:
            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, url: str, **kwargs: object) -> FakeResponse:
                self.url = url
                self.kwargs = kwargs
                return FakeResponse()

        fake = FakeClient()
        old_client = openai_provider.httpx.AsyncClient
        openai_provider.httpx.AsyncClient = lambda **_: fake
        try:
            provider = OpenAIProvider(Settings(openai_api_key="key"))
            chunks = asyncio.run(_collect(provider.generate([Message(role="user", content="hi")])))
        finally:
            openai_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunks[0].content, "hello")
        self.assertEqual(fake.url, "https://api.openai.com/v1/responses")
        self.assertEqual(fake.kwargs["json"]["model"], "gpt-5.4-mini")
        self.assertEqual(fake.kwargs["json"]["input"], [{"role": "user", "content": "hi"}])

    def test_openai_provider_runs_tool_call(self) -> None:
        class FakeResponse:
            status_code = 200

            def __init__(self, body: dict[str, object]) -> None:
                self.body = body

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return self.body

        class FakeClient:
            def __init__(self) -> None:
                self.requests = []
                self.responses = [
                    FakeResponse(
                        {
                            "output": [
                                {
                                    "id": "fc_1",
                                    "call_id": "call_1",
                                    "type": "function_call",
                                    "name": "search_simple",
                                    "arguments": "{\"query\":\"index\"}",
                                }
                            ]
                        }
                    ),
                    FakeResponse({"output_text": "found index", "output": []}),
                ]

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> FakeResponse:
                self.requests.append(kwargs["json"])
                return self.responses.pop(0)

        async def run() -> list[str]:
            async def tool_runner(name: str, arguments: dict[str, object]) -> dict[str, object]:
                self.assertEqual(name, "search_simple")
                self.assertEqual(arguments, {"query": "index"})
                return {"result": "hit"}

            provider = OpenAIProvider(Settings(openai_api_key="key"))
            chunks = await _collect(
                provider.generate(
                    [Message(role="user", content="find index")],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "search_simple",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                    tool_runner=tool_runner,
                )
            )
            return [chunk.type for chunk in chunks]

        fake = FakeClient()
        old_client = openai_provider.httpx.AsyncClient
        openai_provider.httpx.AsyncClient = lambda **_: fake
        try:
            chunk_types = asyncio.run(run())
        finally:
            openai_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunk_types, ["tool_call", "tool_result", "token"])
        self.assertEqual(fake.requests[0]["tools"][0]["name"], "search_simple")
        self.assertEqual(fake.requests[1]["input"][-1]["type"], "function_call_output")
        self.assertEqual(fake.requests[1]["input"][-1]["call_id"], "call_1")

    def test_openai_provider_finalizes_after_tool_round_limit(self) -> None:
        class FakeResponse:
            status_code = 200

            def __init__(self, body: dict[str, object]) -> None:
                self.body = body

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return self.body

        def function_call(call_id: str) -> dict[str, object]:
            return {
                "output": [
                    {
                        "id": f"fc_{call_id}",
                        "call_id": call_id,
                        "type": "function_call",
                        "name": "search_simple",
                        "arguments": "{\"query\":\"index\"}",
                    }
                ]
            }

        class FakeClient:
            def __init__(self) -> None:
                self.requests = []
                self.responses = [
                    FakeResponse(function_call("call_1")),
                    FakeResponse({"output_text": "final answer", "output": []}),
                ]

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> FakeResponse:
                self.requests.append(kwargs["json"])
                return self.responses.pop(0)

        async def run() -> list[str]:
            async def tool_runner(name: str, arguments: dict[str, object]) -> dict[str, object]:
                return {"result": "hit"}

            provider = OpenAIProvider(Settings(openai_api_key="key", chat_mcp_max_rounds=1))
            chunks = await _collect(
                provider.generate(
                    [Message(role="user", content="find index")],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "search_simple",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                    tool_runner=tool_runner,
                )
            )
            return [chunk.type for chunk in chunks]

        fake = FakeClient()
        old_client = openai_provider.httpx.AsyncClient
        openai_provider.httpx.AsyncClient = lambda **_: fake
        try:
            with self.assertLogs("app.models.openai_provider", level="WARNING") as logs:
                chunk_types = asyncio.run(run())
        finally:
            openai_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunk_types, ["tool_call", "tool_result", "token"])
        self.assertNotIn("tools", fake.requests[1])
        self.assertIn("Tool budget reached", fake.requests[1]["input"][-1]["content"])
        self.assertIn("cloud_provider_tool_round_limit_finalize", "\n".join(logs.output))

    def test_cloud_provider_names_blank_http_error(self) -> None:
        class FakeClient:
            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> object:
                raise cloud_provider.httpx.ReadTimeout("")

        old_client = cloud_provider.httpx.AsyncClient
        cloud_provider.httpx.AsyncClient = lambda **_: FakeClient()
        try:
            provider = CloudProvider(Settings(cloud_provider="nvidia", nvidia_api_key="key"))
            with self.assertRaisesRegex(ProviderError, "ReadTimeout"):
                asyncio.run(_collect(provider.generate([Message(role="user", content="hi")])))
        finally:
            cloud_provider.httpx.AsyncClient = old_client

    def test_cloud_provider_classifies_rate_limit(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                request = cloud_provider.httpx.Request(
                    "POST",
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                )
                response = cloud_provider.httpx.Response(429, request=request)
                raise cloud_provider.httpx.HTTPStatusError(
                    "Too Many Requests",
                    request=request,
                    response=response,
                )

        class FakeClient:
            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> FakeResponse:
                return FakeResponse()

        async def run() -> None:
            provider = CloudProvider(Settings(cloud_provider="gemini", gemini_api_key="key"))
            with self.assertRaises(ProviderError) as caught:
                async for _ in provider.generate([Message(role="user", content="hi")]):
                    pass
            self.assertEqual(caught.exception.code, "rate_limited")
            self.assertEqual(caught.exception.status_code, 429)

        old_client = cloud_provider.httpx.AsyncClient
        cloud_provider.httpx.AsyncClient = lambda **_: FakeClient()
        try:
            asyncio.run(run())
        finally:
            cloud_provider.httpx.AsyncClient = old_client

    def test_cloud_provider_runs_tool_call(self) -> None:
        class FakeResponse:
            status_code = 200

            def __init__(self, message: dict[str, object]) -> None:
                self.message = message

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": self.message}]}

        class FakeClient:
            def __init__(self) -> None:
                self.requests = []
                self.responses = [
                    FakeResponse(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_simple",
                                        "arguments": "{\"query\":\"index\"}",
                                    },
                                }
                            ],
                        }
                    ),
                    FakeResponse({"role": "assistant", "content": "found index"}),
                ]

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> FakeResponse:
                self.requests.append(kwargs["json"])
                return self.responses.pop(0)

        async def run() -> list[str]:
            async def tool_runner(name: str, arguments: dict[str, object]) -> dict[str, object]:
                self.assertEqual(name, "search_simple")
                self.assertEqual(arguments, {"query": "index"})
                return {"result": "hit"}

            provider = CloudProvider(Settings(cloud_provider="gemini", gemini_api_key="key"))
            chunks = await _collect(
                provider.generate(
                    [Message(role="user", content="find index")],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "search_simple",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                    tool_runner=tool_runner,
                )
            )
            return [chunk.type for chunk in chunks]

        fake = FakeClient()
        old_client = cloud_provider.httpx.AsyncClient
        cloud_provider.httpx.AsyncClient = lambda **_: fake
        try:
            with self.assertLogs("app.models.cloud_provider", level="INFO") as logs:
                chunk_types = asyncio.run(run())
        finally:
            cloud_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunk_types, ["tool_call", "tool_result", "token"])
        self.assertEqual(fake.requests[1]["messages"][-1]["role"], "tool")
        self.assertEqual(fake.requests[1]["messages"][-1]["name"], "search_simple")
        self.assertIn("cloud_provider_tool_call_requested", "\n".join(logs.output))

    def test_cloud_provider_finalizes_after_tool_round_limit(self) -> None:
        class FakeResponse:
            status_code = 200

            def __init__(self, message: dict[str, object]) -> None:
                self.message = message

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": self.message}]}

        def tool_message(call_id: str) -> dict[str, object]:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "search_simple",
                            "arguments": "{\"query\":\"index\"}",
                        },
                    }
                ],
            }

        class FakeClient:
            def __init__(self) -> None:
                self.requests = []
                self.responses = [
                    FakeResponse(tool_message("call_1")),
                    FakeResponse(tool_message("call_2")),
                    FakeResponse({"role": "assistant", "content": "final answer"}),
                ]

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> FakeResponse:
                self.requests.append(kwargs["json"])
                return self.responses.pop(0)

        async def run() -> list[str]:
            async def tool_runner(name: str, arguments: dict[str, object]) -> dict[str, object]:
                return {"result": f"{name}:{arguments['query']}"}

            provider = CloudProvider(
                Settings(cloud_provider="gemini", gemini_api_key="key", chat_mcp_max_rounds=2)
            )
            chunks = await _collect(
                provider.generate(
                    [Message(role="user", content="find index")],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "search_simple",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                    tool_runner=tool_runner,
                )
            )
            return [chunk.type for chunk in chunks]

        fake = FakeClient()
        old_client = cloud_provider.httpx.AsyncClient
        cloud_provider.httpx.AsyncClient = lambda **_: fake
        try:
            with self.assertLogs("app.models.cloud_provider", level="WARNING") as logs:
                chunk_types = asyncio.run(run())
        finally:
            cloud_provider.httpx.AsyncClient = old_client

        self.assertEqual(chunk_types, ["tool_call", "tool_result", "tool_call", "tool_result", "token"])
        self.assertNotIn("tools", fake.requests[2])
        self.assertNotIn("tool_choice", fake.requests[2])
        self.assertIn("Tool budget reached", fake.requests[2]["messages"][-1]["content"])
        self.assertIn("cloud_provider_tool_round_limit_finalize", "\n".join(logs.output))

    def test_cloud_provider_maps_http_error_codes(self) -> None:
        self.assertEqual(cloud_provider._http_error_code(400), "provider_bad_request")
        self.assertEqual(cloud_provider._http_error_code(413), "provider_payload_too_large")
        self.assertEqual(cloud_provider._http_error_code(429), "rate_limited")
        self.assertEqual(cloud_provider._http_error_code(500), "provider_http_status")

    def test_chat_rejects_write_tool(self) -> None:
        with self.assertRaisesRegex(ProviderError, "not allowed"):
            asyncio.run(chat_api._run_chat_tool("vault_write", {}))

    def test_chat_respects_custom_tool_allowlist(self) -> None:
        with self.assertRaisesRegex(ProviderError, "not allowed"):
            asyncio.run(chat_api._run_chat_tool("vault_read", {}, {"search_simple"}))

    def test_chat_tools_respects_custom_allowlist(self) -> None:
        async def fake_list_tools() -> list[dict[str, object]]:
            return [
                {"name": "search_simple", "inputSchema": {"type": "object"}},
                {"name": "vault_read", "inputSchema": {"type": "object"}},
            ]

        old_list_tools = chat_api.list_tools
        chat_api.list_tools = fake_list_tools
        try:
            tools = asyncio.run(chat_api._chat_tools({"search_simple"}))
        finally:
            chat_api.list_tools = old_list_tools

        self.assertEqual([tool["function"]["name"] for tool in tools], ["search_simple"])

    def test_record_presentation_vault_read_source(self) -> None:
        sources: list[dict[str, str]] = []
        sections: list[dict[str, str]] = []

        chat_api._record_presentation(
            "vault_read",
            {"path": "00-Index.md"},
            {"content": "Main vault index content"},
            sources,
            set(),
            sections,
        )

        self.assertEqual(
            sources,
            [
                {
                    "title": "00-Index.md",
                    "path": "00-Index.md",
                    "tool": "vault_read",
                    "excerpt": "Main vault index content",
                }
            ],
        )
        self.assertEqual(sections, [])

    def test_record_presentation_vault_list_section(self) -> None:
        sources: list[dict[str, str]] = []
        sections: list[dict[str, str]] = []

        chat_api._record_presentation(
            "vault_list",
            {"path": "02-Personal"},
            {"entries": ["vault-mind-backend/", "vault-mind/"]},
            sources,
            set(),
            sections,
        )

        self.assertEqual(sources, [])
        self.assertEqual(sections[0]["title"], "Folder: 02-Personal")
        self.assertIn("- `vault-mind-backend/`", sections[0]["content"])

    def test_record_presentation_search_sources_are_deduped(self) -> None:
        sources: list[dict[str, str]] = []
        sections: list[dict[str, str]] = []
        seen: set[str] = set()

        chat_api._record_presentation(
            "search_simple",
            {"query": "index"},
            [
                {"filename": "00-Index.md", "matches": [{"context": "first hit"}]},
                {"filename": "00-Index.md", "matches": [{"context": "duplicate hit"}]},
            ],
            sources,
            seen,
            sections,
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["path"], "00-Index.md")
        self.assertEqual(sources[0]["excerpt"], "first hit")

    def test_chat_stream_emits_presentation_events_before_done(self) -> None:
        class FakeProvider:
            name = "cloud"

            async def generate(self, messages: list[Message], **kwargs: object):
                await kwargs["tool_runner"]("vault_read", {"path": "00-Index.md"})
                yield Chunk(type="token", content="answer")

        async def fake_chat_tools(*args: object, **kwargs: object) -> list[dict[str, object]]:
            return []

        async def fake_run_tool(
            name: str,
            arguments: dict[str, object],
            allowed_tools: set[str],
        ) -> dict[str, object]:
            if name == "search_simple":
                return []
            self.assertEqual(name, "vault_read")
            self.assertEqual(arguments, {"path": "00-Index.md"})
            return {"content": "Main vault index content"}

        async def run() -> list[dict[str, object]]:
            response = await chat_api.chat(ChatRequest(message="hello"))
            items = []
            async for item in response.body_iterator:
                if isinstance(item, bytes):
                    item = item.decode()
                items.append(json.loads(item.removeprefix("data: ").strip()))
            return items

        old_provider = chat_api.get_provider
        old_chat_tools = chat_api._chat_tools
        old_run_tool = chat_api._run_chat_tool
        old_settings = chat_api.settings
        chat_api.get_provider = lambda *args, **kwargs: FakeProvider()
        chat_api._chat_tools = fake_chat_tools
        chat_api._run_chat_tool = fake_run_tool
        chat_api.settings = Settings(chat_mcp_enabled=True)
        try:
            events = asyncio.run(run())
        finally:
            chat_api.get_provider = old_provider
            chat_api._chat_tools = old_chat_tools
            chat_api._run_chat_tool = old_run_tool
            chat_api.settings = old_settings

        self.assertEqual([item["type"] for item in events], ["token", "sources", "done"])
        self.assertEqual(events[1]["sources"][0]["path"], "00-Index.md")

    def test_chat_grounding_reads_vault_content_before_provider(self) -> None:
        captured: dict[str, object] = {}
        calls: list[tuple[str, dict[str, object]]] = []

        class FakeProvider:
            name = "cloud"

            async def generate(self, messages: list[Message], **kwargs: object):
                captured["messages"] = messages
                yield Chunk(type="token", content="answer")

        async def fake_chat_tools(*args: object, **kwargs: object) -> list[dict[str, object]]:
            return []

        async def fake_run_tool(
            name: str,
            arguments: dict[str, object],
            allowed_tools: set[str],
        ) -> dict[str, object]:
            calls.append((name, arguments))
            if name == "vault_read" and arguments == {"path": "00-Index.md"}:
                return {"content": "[[02-Personal/linux-notepad-plus/README.md|notepad++]]"}
            if name == "vault_read":
                return {"content": "Linux Notepad Plus is a personal Linux editor project."}
            self.fail(f"unexpected tool: {name}")

        async def run() -> list[dict[str, object]]:
            response = await chat_api.chat(ChatRequest(message="what is my notepad++ project?"))
            items = []
            async for item in response.body_iterator:
                if isinstance(item, bytes):
                    item = item.decode()
                items.append(json.loads(item.removeprefix("data: ").strip()))
            return items

        old_provider = chat_api.get_provider
        old_chat_tools = chat_api._chat_tools
        old_run_tool = chat_api._run_chat_tool
        old_settings = chat_api.settings
        chat_api.get_provider = lambda *args, **kwargs: FakeProvider()
        chat_api._chat_tools = fake_chat_tools
        chat_api._run_chat_tool = fake_run_tool
        chat_api.settings = Settings(chat_mcp_enabled=True)
        try:
            events = asyncio.run(run())
        finally:
            chat_api.get_provider = old_provider
            chat_api._chat_tools = old_chat_tools
            chat_api._run_chat_tool = old_run_tool
            chat_api.settings = old_settings

        messages = captured["messages"]
        self.assertEqual(messages[0].role, "system")
        self.assertIn("Vault Context", messages[0].content)
        self.assertIn("Linux Notepad Plus is a personal Linux editor project", messages[0].content)
        self.assertEqual(messages[1].content, "what is my notepad++ project?")
        self.assertEqual(calls[0], ("vault_read", {"path": "00-Index.md"}))
        self.assertEqual(calls[1], ("vault_read", {"path": "02-Personal/linux-notepad-plus/README.md"}))
        self.assertEqual(calls[2], ("vault_read", {"path": "02-Personal/linux-notepad-plus/state.md"}))
        self.assertEqual([item["type"] for item in events], ["token", "sources", "done"])
        self.assertEqual(
            [source["path"] for source in events[1]["sources"]],
            ["00-Index.md", "02-Personal/linux-notepad-plus/README.md", "02-Personal/linux-notepad-plus/state.md"],
        )

    def test_chat_logs_mcp_tool_summary(self) -> None:
        async def fake_call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
            self.assertEqual(name, "search_simple")
            self.assertEqual(arguments, {"query": "index"})
            return {"content": "vault content"}

        old_call_tool = chat_api.call_tool
        chat_api.call_tool = fake_call_tool
        try:
            with self.assertLogs("app.api.chat", level="INFO") as logs:
                result = asyncio.run(chat_api._run_chat_tool("search_simple", {"query": "index"}))
        finally:
            chat_api.call_tool = old_call_tool

        self.assertEqual(result, {"content": "vault content"})
        output = "\n".join(logs.output)
        self.assertIn("chat_mcp_tool_request", output)
        self.assertIn("chat_mcp_tool_response", output)
        self.assertNotIn("vault content", output)

    def test_mcp_status_disabled(self) -> None:
        old_url = mcp_client.settings.mcp_server_url
        mcp_client.settings.__dict__["mcp_server_url"] = ""
        try:
            response = asyncio.run(mcp_client.status())
        finally:
            mcp_client.settings.__dict__["mcp_server_url"] = old_url

        self.assertEqual(response["configured"], False)
        self.assertEqual(response["ok"], True)

    def test_mcp_status_lists_tools(self) -> None:
        async def fake_list_tools():
            return [{"name": "search_simple"}]

        old_url = mcp_client.settings.mcp_server_url
        old_list_tools = mcp_client.list_tools
        mcp_client.settings.__dict__["mcp_server_url"] = "http://127.0.0.1:27123/mcp/"
        mcp_client.list_tools = fake_list_tools
        try:
            response = asyncio.run(mcp_client.status())
        finally:
            mcp_client.settings.__dict__["mcp_server_url"] = old_url
            mcp_client.list_tools = old_list_tools

        self.assertEqual(response["ok"], True)
        self.assertEqual(response["tools"], ["search_simple"])


async def _collect(iterator):
    return [chunk async for chunk in iterator]


if __name__ == "__main__":
    unittest.main()
