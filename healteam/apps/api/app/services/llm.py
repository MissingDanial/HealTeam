"""LLM client interface and W1 stub.

W1 ships a `StubLLMClient` that returns deterministic placeholder text and
NEVER makes a network call. W2 (`integration-verify` task) replaces it with
an OpenAI-compatible client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel


class LLMConfig(BaseModel):
    """Configuration for the LLM client. Loaded from env vars in `main.py`."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 512

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "512")),
        )


@dataclass
class StubLLMClient:
    """W1 stub. Echoes the system prompt and a count of user messages."""

    config: LLMConfig

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> str:
        n_user = sum(1 for m in messages if m.get("role") == "user")
        sys_head = (system[:60] + "...") if len(system) > 60 else system
        return f"[stub-llm | {n_user} user msg(s) | sys='{sys_head}']"


__all__ = ["LLMConfig", "StubLLMClient"]
