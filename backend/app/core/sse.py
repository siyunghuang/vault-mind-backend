import json
from collections.abc import AsyncIterator
from typing import Any


def event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def stream_error(message: str, code: str = "error", status_code: int = 500) -> AsyncIterator[str]:
    yield event({"type": "error", "message": message, "code": code, "status_code": status_code})
    yield event({"type": "done"})
