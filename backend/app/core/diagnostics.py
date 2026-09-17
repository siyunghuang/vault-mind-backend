from contextvars import ContextVar
from dataclasses import dataclass
import logging
from typing import Any


@dataclass
class ChatDiagnostics:
    request_id: str
    tool_executions: int = 0
    cache_hits: int = 0
    ai_rounds: int = 0
    notes_extracted: int = 0
    context_chars: int = 0


chat_diagnostics: ContextVar[ChatDiagnostics | None] = ContextVar("chat_diagnostics", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        diagnostics = chat_diagnostics.get()
        record.request_id = diagnostics.request_id if diagnostics else "-"
        return True


def record_ai_request() -> None:
    diagnostics = chat_diagnostics.get()
    if diagnostics:
        diagnostics.ai_rounds += 1


def log_usage(provider: str, model: str, usage: Any) -> None:
    usage = usage if isinstance(usage, dict) else {}
    logging.getLogger(__name__).info(
        "provider_usage provider=%s model=%s input_tokens=%s output_tokens=%s",
        provider, model,
        usage.get("input_tokens", usage.get("prompt_tokens", "unavailable")),
        usage.get("output_tokens", usage.get("completion_tokens", "unavailable")),
    )
