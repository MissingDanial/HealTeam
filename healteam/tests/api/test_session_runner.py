from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.services.session_runner import (  # noqa: E402
    AnthropicLLMClient,
    build_session_llm_client,
    load_session_llm_settings,
)


def test_load_session_llm_settings_prefers_anthropic_model(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    monkeypatch.setenv("LLM_MODEL", "fallback-model")

    settings = load_session_llm_settings()

    assert settings.model == "claude-test"


def test_build_session_llm_client_returns_none_without_token(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    client = build_session_llm_client()

    assert client is None


def test_build_session_llm_client_uses_env_settings(monkeypatch) -> None:
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "live")
    monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com/anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")

    client = build_session_llm_client()

    assert isinstance(client, AnthropicLLMClient)
    assert client.model == "claude-test"
