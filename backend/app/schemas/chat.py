from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    provider: Literal["cloud", "local"] | None = None
    model_id: str | None = None
