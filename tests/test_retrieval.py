import asyncio
import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.api import chat as chat_api
from app.core.config import Settings
from app.core.diagnostics import RequestIdFilter, chat_diagnostics, log_usage
from app.main import app
from app.models.base import Chunk, ProviderError


PROJECT = "02-Personal/linux-notepad-plus/README.md"
STATE = "02-Personal/linux-notepad-plus/state.md"
INDEX = f"## Projects\n[[{PROJECT}|Linux Notepad Plus]] ([[{PROJECT}|notepad++]])"


def envelope(value):
    return {"content": [{"type": "text", "text": json.dumps(value)}], "structuredContent": None, "isError": False}


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    def test_decode_and_presentation(self):
        note = {"path": PROJECT, "content": "Linux editor content"}
        for result in (envelope(note), {**envelope("ignored"), "structuredContent": note}, note):
            self.assertEqual(chat_api._decode_tool_result(result), note)
        self.assertEqual(chat_api._decode_tool_result({"content": [{"type": "text", "text": "plain markdown"}]}), "plain markdown")
        self.assertEqual(chat_api._decode_tool_result({"content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}), "first\nsecond")
        self.assertEqual(chat_api._decode_tool_result({"structuredContent": [], "content": []}), [])
        with self.assertRaises(ProviderError) as error:
            chat_api._decode_tool_result({**envelope(note), "isError": True})
        self.assertEqual(error.exception.code, "mcp_tool_failed")

        sources, sections = [], []
        chat_api._record_presentation("vault_read", {"path": PROJECT}, chat_api._decode_tool_result(envelope(note)), sources, set(), sections)
        self.assertEqual(sources[0]["excerpt"], "Linux editor content")
        search = chat_api._decode_tool_result(envelope([{"filename": STATE, "matches": [{"context": "Current status"}]}]))
        self.assertEqual(chat_api._search_sources(search), [(STATE, {"context": "Current status"})])
        chat_api._record_presentation("search_simple", {}, search, sources, {PROJECT}, sections)
        self.assertEqual(sources[1]["excerpt"], "Current status")
        chat_api._record_presentation("vault_list", {}, chat_api._decode_tool_result(envelope(["notes/", "index.md"])), sources, set(), sections)
        self.assertIn("notes/", sections[0]["content"])

    def test_project_identity_and_limits(self):
        for query in ("tell me about my notepad++ project", "LINUX NOTEPAD PLUS", "linux-notepad-plus"):
            self.assertEqual(chat_api._project_matches(INDEX, query), {PROJECT})
        self.assertEqual(chat_api._project_matches(INDEX, "notepad"), set())
        self.assertEqual(chat_api._project_matches(INDEX, "notepad+++"), set())
        ambiguous = INDEX + "\n[[02-Personal/another/README|notepad++]]"
        self.assertEqual(len(chat_api._project_matches(ambiguous, "notepad++")), 2)
        self.assertEqual(chat_api._project_matches("[[02-Personal/../README|notepad++]]", "notepad++"), set())
        related = "[[02-Personal/vault-mind/README]] [[02-Personal/vault-mind-backend/README]]"
        self.assertEqual(chat_api._project_matches(related, "vault-mind-backend"), {"02-Personal/vault-mind-backend/README.md"})
        context = chat_api._build_vault_context([("index", "x" * 20000), (PROJECT, "should not fit")])
        self.assertLessEqual(len(context), 12000)
        self.assertNotIn("should not fit", context)
        context = chat_api._build_vault_context([(str(i), "content") for i in range(5)])
        self.assertNotIn("## 4", context)

    async def test_real_envelopes_preload_and_cache_per_chat(self):
        test = self

        class Provider:
            name = "cloud"

            async def generate(self, messages, **kwargs):
                test.assertIn("Linux-native editor identity", messages[0].content)
                test.assertIn("Current Linux project status", messages[0].content)
                for path in ("00-Index.md", PROJECT, STATE):
                    result = await kwargs["tool_runner"]("vault_read", {"path": path})
                    test.assertIsInstance(result["content"], str)
                await kwargs["tool_runner"]("search_simple", {"query": "editor", "contextLength": 80})
                await kwargs["tool_runner"]("search_simple", {"contextLength": 80, "query": "editor"})
                yield Chunk("token", "Linux project answer")

        notes = {"00-Index.md": INDEX, PROJECT: "Linux-native editor identity", STATE: "Current Linux project status"}
        async def tool(name, arguments):
            if name == "vault_read":
                return envelope({"path": arguments["path"], "content": notes[arguments["path"]]})
            return envelope([])

        call = AsyncMock(side_effect=tool)
        with patch.object(chat_api, "settings", Settings(chat_mcp_enabled=True)), \
             patch.object(chat_api, "get_provider", return_value=Provider()), \
             patch.object(chat_api, "list_tools", return_value=[]), \
             patch.object(chat_api, "call_tool", call):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for _ in range(2):
                    with self.assertLogs("app.api.chat", level="INFO") as logs:
                        response = await client.post("/api/chat", json={"message": "tell me about my notepad++ project"})
                    self.assertIn("tool_executions=4 cache_hits=4", "\n".join(logs.output))
                    self.assertIn("notes_extracted=3", "\n".join(logs.output))
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    self.assertEqual([event["type"] for event in events], ["token", "sources", "done"])
                    self.assertEqual([source["path"] for source in events[1]["sources"]], list(notes))
        self.assertEqual(call.await_count, 8)
        self.assertFalse(any(call.args[1].get("query") == "tell me about my notepad++ project" for call in call.await_args_list))

    async def test_ambiguous_and_unmatched_only_preload_index(self):
        for index, query in ((INDEX, "unrelated question"), (INDEX + "\n[[02-Personal/other/README|notepad++]]", "notepad++")):
            with patch.object(chat_api, "call_tool", return_value=envelope({"content": index})) as tool:
                await chat_api._ground_chat_in_vault(query, [], set(), [])
            tool.assert_awaited_once_with("vault_read", {"path": "00-Index.md"})

    async def test_empty_required_note_finishes_with_error(self):
        with patch.object(chat_api, "settings", Settings(chat_mcp_enabled=True)), \
             patch.object(chat_api, "get_provider") as factory, \
             patch.object(chat_api, "call_tool", return_value=envelope({"path": "00-Index.md"})), \
             patch.object(chat_api, "list_tools") as listing:
            factory.return_value.name = "cloud"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/chat", json={"message": "hello"})
            self.assertIn('"code":"mcp_bad_response"', response.text)
            self.assertTrue(response.text.endswith('data: {"type":"done"}\n\n'))
            listing.assert_not_called()

    async def test_request_ids_are_isolated_and_exposed(self):
        observed = []
        class Provider:
            name = "local"

            async def generate(self, messages, **kwargs):
                before = chat_diagnostics.get().request_id
                await asyncio.sleep(0)
                record = logging.makeLogRecord({"msg": "diagnostic"})
                RequestIdFilter().filter(record)
                observed.append((before, record.request_id))
                yield Chunk("token", before)

        with patch.object(chat_api, "get_provider", return_value=Provider()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                responses = await asyncio.gather(*[
                    client.post("/api/chat", json={"message": "hello"}, headers={"Origin": Settings().frontend_origin})
                    for _ in range(2)
                ])
        ids = [response.headers["X-Request-ID"] for response in responses]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(set(observed), {(value, value) for value in ids})
        self.assertIn("X-Request-ID", responses[0].headers["access-control-expose-headers"])
        self.assertIsNone(chat_diagnostics.get())

    def test_usage_reports_values_or_unavailable(self):
        for usage in ({"input_tokens": 10, "output_tokens": 2}, {"prompt_tokens": 10, "completion_tokens": 2}):
            with self.assertLogs("app.core.diagnostics", level="INFO") as logs:
                log_usage("test", "model", usage)
            self.assertIn("input_tokens=10 output_tokens=2", logs.output[0])
        with self.assertLogs("app.core.diagnostics", level="INFO") as logs:
            log_usage("test", "model", None)
        self.assertIn("input_tokens=unavailable output_tokens=unavailable", logs.output[0])
