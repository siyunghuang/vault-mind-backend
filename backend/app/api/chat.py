from collections.abc import AsyncIterator
import json
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.sse import event, stream_error
from app.models.base import Message, ProviderError
from app.models.factory import get_provider
from app.mcp.client import McpError, call_tool, list_tools
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)
SECRET_KEYS = ("api_key", "authorization", "bearer", "password", "secret", "token")

CHAT_MCP_TOOLS = {"search_simple", "search_query", "vault_read", "vault_list"}


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


def _title_from_path(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or path or "Vault"


def _text_from_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "result", "context", "excerpt"):
            item = value.get(key)
            if isinstance(item, str):
                return item
    return ""


def _excerpt(value: Any, limit: int = 240) -> str | None:
    text = " ".join(_text_from_result(value).split())
    if not text:
        return None
    return text if len(text) <= limit else f"{text[:limit]}..."


def _add_source(
    sources: list[dict[str, str]],
    seen_paths: set[str],
    path: str,
    result: Any,
    tool: str,
) -> None:
    if not path or path in seen_paths:
        return
    seen_paths.add(path)
    source = {"title": _title_from_path(path), "path": path, "tool": tool}
    excerpt = _excerpt(result)
    if excerpt:
        source["excerpt"] = excerpt
    sources.append(source)


def _list_entries(result: Any) -> list[str]:
    if isinstance(result, list):
        return [str(item) for item in result]
    if not isinstance(result, dict):
        return []
    for key in ("entries", "items", "files", "children", "result"):
        value = result.get(key)
        if isinstance(value, list):
            return [str(item.get("name", item)) if isinstance(item, dict) else str(item) for item in value]
    return []


def _search_sources(result: Any) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(result, list):
        for item in result:
            found.extend(_search_sources(item))
    elif isinstance(result, dict):
        path = result.get("path") or result.get("filename") or result.get("file")
        if isinstance(path, str):
            matches = result.get("matches")
            found.append((path, matches[0] if isinstance(matches, list) and matches else result))
        for value in result.values():
            if isinstance(value, (list, dict)):
                found.extend(_search_sources(value))
    return found


def _record_presentation(
    tool: str,
    arguments: dict[str, Any],
    result: Any,
    sources: list[dict[str, str]],
    seen_paths: set[str],
    sections: list[dict[str, str]],
) -> None:
    if tool == "vault_read":
        _add_source(sources, seen_paths, str(arguments.get("path", "")), result, tool)
        return
    if tool == "vault_list":
        entries = _list_entries(result)
        if entries:
            path = str(arguments.get("path") or "")
            title = f"Folder: {path}" if path else "Vault Listing"
            sections.append({"title": title, "content": "\n".join(f"- `{entry}`" for entry in entries)})
        return
    if tool in {"search_simple", "search_query"}:
        for path, match in _search_sources(result):
            _add_source(sources, seen_paths, path, match, tool)


async def _chat_tools(allowed_tools: set[str] = CHAT_MCP_TOOLS) -> list[dict[str, Any]]:
    tools = []
    try:
        mcp_tools = await list_tools()
    except McpError as exc:
        logger.warning("chat_mcp_tools_unavailable error=%s", exc)
        raise ProviderError(
            f"MCP tools unavailable: {exc}",
            code="mcp_unavailable",
            status_code=503,
        ) from exc
    for tool in mcp_tools:
        name = tool.get("name")
        if name not in allowed_tools:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or name,
                    "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                },
            }
        )
    logger.info("chat_mcp_tools_loaded available=%s allowed=%s", len(mcp_tools), len(tools))
    return tools


async def _run_chat_tool(
    name: str,
    arguments: dict[str, Any],
    allowed_tools: set[str] = CHAT_MCP_TOOLS,
) -> dict[str, Any]:
    if name not in allowed_tools:
        logger.warning("chat_mcp_tool_blocked tool=%s", name)
        raise ProviderError(
            f"MCP tool not allowed in chat: {name}",
            code="mcp_tool_not_allowed",
            status_code=403,
        )
    try:
        extra = ""
        if settings.log_payloads:
            extra = f" args_preview={_preview(arguments, settings.log_payload_chars)}"
        logger.info(
            "chat_mcp_tool_request status=started transport=obsidian tool=%s arg_keys=%s args_bytes=%s%s",
            name,
            sorted(arguments.keys()),
            _json_len(arguments),
            extra,
        )
        started = time.perf_counter()
        result = await call_tool(name, arguments)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result_extra = ""
        if settings.log_payloads:
            result_extra = f" result_preview={_preview(result, settings.log_payload_chars)}"
        logger.info(
            "chat_mcp_tool_response status=completed transport=obsidian tool=%s elapsed_ms=%s result_type=%s result_bytes=%s%s",
            name,
            elapsed_ms,
            type(result).__name__,
            _json_len(result),
            result_extra,
        )
        return result
    except McpError as exc:
        logger.warning("chat_mcp_tool_failed tool=%s error=%s", name, exc)
        raise ProviderError(
            f"MCP tool failed: {exc}",
            code="mcp_tool_failed",
            status_code=502,
        ) from exc


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        try:
            provider = get_provider(request.provider, request.model_id)
            logger.info(
                "chat_start provider=%s requested_provider=%s model_id=%s mcp_enabled=%s session_id_present=%s message_chars=%s",
                provider.name,
                request.provider,
                request.model_id,
                settings.chat_mcp_enabled,
                bool(request.session_id),
                len(request.message),
            )
            kwargs: dict[str, Any] = {}
            presentation_sources: list[dict[str, str]] = []
            presentation_source_paths: set[str] = set()
            presentation_sections: list[dict[str, str]] = []
            if settings.chat_mcp_enabled and provider.name == "cloud":
                async def tool_runner(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    result = await _run_chat_tool(name, arguments, CHAT_MCP_TOOLS)
                    _record_presentation(
                        name,
                        arguments,
                        result,
                        presentation_sources,
                        presentation_source_paths,
                        presentation_sections,
                    )
                    return result

                kwargs = {"tools": await _chat_tools(CHAT_MCP_TOOLS), "tool_runner": tool_runner}
            async for chunk in provider.generate([Message(role="user", content=request.message)], **kwargs):
                yield event({"type": chunk.type, "content": chunk.content})
            if presentation_sources:
                yield event({"type": "sources", "sources": presentation_sources})
            if presentation_sections:
                yield event({"type": "sections", "sections": presentation_sections})
            yield event({"type": "done"})
        except ProviderError as exc:
            logger.warning(
                "chat_error code=%s status_code=%s message=%s",
                exc.code,
                exc.status_code,
                exc,
            )
            async for item in stream_error(str(exc), code=exc.code, status_code=exc.status_code):
                yield item
        except ValueError as exc:
            logger.warning("chat_bad_request error=%s", exc)
            async for item in stream_error(str(exc), code="bad_request", status_code=400):
                yield item

    return StreamingResponse(body(), media_type="text/event-stream")
