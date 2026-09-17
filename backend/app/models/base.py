from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from typing import Protocol


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str = "provider_error",
        status_code: int = 503,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Chunk:
    type: str
    content: str = ""


class ModelProvider(Protocol):
    name: str

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_runner: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> AsyncIterator[Chunk]:
        ...

    async def health(self) -> dict[str, object]:
        ...
