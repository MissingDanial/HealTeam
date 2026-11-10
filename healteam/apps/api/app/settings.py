"""Explicit runtime configuration; secrets never belong in public status data."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class ConfigurationError(ValueError):
    """A safe error that does not contain environment values."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model_mode: Literal["mock", "live"] = "mock"
    provider: Literal["anthropic", "openai", "deepseek"] = "anthropic"
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    base_url: str | None = Field(default=None, exclude=True, repr=False)
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0, le=1)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    review_max_tokens: int = Field(default=4096, ge=1024, le=32768)
    send_temperature: bool = False
    max_response_bytes: int = Field(default=262144, ge=1024, le=2097152)
    model_max_calls: int = Field(default=128, ge=1, le=512)
    model_token_budget: int = Field(default=250000, ge=1, le=10000000)
    context_max_chars: int = Field(default=30000, ge=1024, le=200000)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    operation_timeout_seconds: float = Field(default=60, gt=0, le=600)
    stop_timeout_seconds: float = Field(default=2, gt=0, le=10)
    storage_busy_timeout_seconds: float = Field(default=2, gt=0, le=30)
    database_url: str | None = Field(default=None, exclude=True, repr=False)

    def check_live_configuration(self) -> None:
        if self.model_mode != "live":
            return
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ConfigurationError("live_requires_api_key")
        if not self.model or not self.model.strip():
            raise ConfigurationError("live_requires_model")
        if self.base_url:
            try:
                parsed = urlsplit(self.base_url)
            except ValueError:
                raise ConfigurationError("invalid_llm_base_url") from None
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ConfigurationError("invalid_llm_base_url")

    def public_status(self) -> dict[str, object]:
        return {
            "model_mode": self.model_mode,
            "provider": self.provider,
            "model": self.model if self.model_mode == "live" else None,
        }


def _first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def load_settings(*, require_live_credentials: bool = True) -> Settings:
    """Unified HEALTEAM variables override provider legacy names."""
    provider = _first("HEALTEAM_LLM_PROVIDER", default="anthropic")
    key_names = {
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        "openai": ("OPENAI_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
    }.get(provider, ())
    prefix = {"anthropic": "ANTHROPIC", "openai": "OPENAI", "deepseek": "DEEPSEEK"}.get(provider, "LLM")
    try:
        settings = Settings(
            model_mode=_first("HEALTEAM_MODEL_MODE", default="mock"),
            provider=provider,
            api_key=_first("HEALTEAM_LLM_API_KEY", *key_names),
            base_url=_first("HEALTEAM_LLM_BASE_URL", f"{prefix}_BASE_URL") or None,
            model=_first("HEALTEAM_LLM_MODEL", f"{prefix}_MODEL", "LLM_MODEL") or None,
            temperature=_first("HEALTEAM_LLM_TEMPERATURE", "LLM_TEMPERATURE", default="0.7"),
            max_tokens=_first("HEALTEAM_LLM_MAX_TOKENS", "LLM_MAX_TOKENS", default="1024"),
            review_max_tokens=_first("HEALTEAM_REVIEW_MAX_TOKENS", default="4096"),
            send_temperature=_first("HEALTEAM_LLM_SEND_TEMPERATURE", default="false"),
            max_response_bytes=_first("HEALTEAM_LLM_MAX_RESPONSE_BYTES", default="262144"),
            model_max_calls=_first("HEALTEAM_MODEL_MAX_CALLS", default="128"),
            model_token_budget=_first("HEALTEAM_MODEL_TOKEN_BUDGET", default="250000"),
            context_max_chars=_first("HEALTEAM_CONTEXT_MAX_CHARS", default="30000"),
            timeout_seconds=_first("HEALTEAM_LLM_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS", default="60"),
            operation_timeout_seconds=_first("HEALTEAM_OPERATION_TIMEOUT_SECONDS", default="60"),
            stop_timeout_seconds=_first("HEALTEAM_STOP_TIMEOUT_SECONDS", default="2"),
            storage_busy_timeout_seconds=_first("HEALTEAM_STORAGE_BUSY_TIMEOUT_SECONDS", default="2"),
            database_url=_first("HEALTEAM_DATABASE_URL"),
        )
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError("invalid_configuration:" + ",".join(fields)) from None
    if require_live_credentials:
        settings.check_live_configuration()
    return settings
