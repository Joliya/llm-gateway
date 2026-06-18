from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = None


class ChatCompletionRequest(BaseModel):
    """OpenAI /v1/chat/completions request. Unknown fields are preserved."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None

    def to_upstream_dict(self) -> dict[str, Any]:
        """Full param dict including extras, dropping None top-level fields."""
        data = self.model_dump(exclude_none=True)
        return data


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    input: Any


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    prompt: Any
    stream: bool = False
