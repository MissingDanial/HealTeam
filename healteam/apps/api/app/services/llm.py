"""One-shot text adapters with safe errors and bounded provider responses.

The S3 path is complete(). chat() retains the retired S0 runner's payload and
string-result compatibility; durable live sessions do not use that path.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..settings import Settings, load_settings


@dataclass(frozen=True)
class ModelResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str = "stop"
    latency_ms: int = 0


class LLMRequestError(RuntimeError):
    """Only safe codes and token counts, never provider bodies or credentials."""

    def __init__(
        self,
        code: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ):
        self.code = code
        self.input_tokens = input_tokens if type(input_tokens) is int and input_tokens >= 0 else None
        self.output_tokens = output_tokens if type(output_tokens) is int and output_tokens >= 0 else None
        super().__init__(code)


class HTTPModelClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int | None = None,
    ):
        settings.check_live_configuration()
        limit = (
            getattr(settings, "max_response_bytes", 262_144)
            if max_response_bytes is None
            else max_response_bytes
        )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("max_response_bytes must be positive")
        self.settings = settings
        self.model = settings.model
        self.transport = transport
        self.max_response_bytes = limit
        # Only the retired runner reads this compatibility signal.
        self.failure: str | None = None

    def _error(self, code: str, **usage: Any) -> LLMRequestError:
        self.failure = code
        return LLMRequestError(code, **usage)

    def _endpoint(self) -> str:
        settings = self.settings
        default = {
            "anthropic": "https://api.anthropic.com",
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
        }[settings.provider]
        try:
            base = settings.base_url or default
            if any(ord(character) <= 32 or ord(character) == 127 for character in base):
                raise ValueError
            parsed = urlsplit(base)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise self._error("llm_invalid_endpoint") from None
        path = parsed.path.rstrip("/")
        if path.endswith(("/messages", "/chat/completions", "/responses")):
            raise self._error("llm_invalid_endpoint")
        if settings.provider == "anthropic":
            suffix = "/messages" if path.endswith("/v1") else "/v1/messages"
        else:
            suffix = "/chat/completions"
        return urlunsplit((parsed.scheme, parsed.netloc, path + suffix, "", ""))

    def _payload(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        legacy: bool,
    ) -> dict[str, Any]:
        settings = self.settings
        selected_model = settings.model if model is None else model
        if not isinstance(selected_model, str) or not selected_model.strip():
            raise self._error("llm_invalid_request")
        if (
            not isinstance(system, str)
            or not system.strip()
            or not isinstance(messages, list)
            or not messages
        ):
            raise self._error("llm_invalid_request")
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or not isinstance(message["role"], str)
                or message["role"] not in {"user", "assistant"}
                or not isinstance(message["content"], str)
                or not message["content"].strip()
            ):
                raise self._error("llm_invalid_request")
        requested = settings.max_tokens if max_tokens is None else max_tokens
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
            raise self._error("llm_invalid_request")
        payload: dict[str, Any] = {"model": selected_model, "stream": False}
        output_key = (
            "max_tokens"
            if legacy or settings.provider in {"anthropic", "deepseek"}
            else "max_completion_tokens"
        )
        payload[output_key] = min(requested, settings.max_tokens)
        if settings.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        if legacy or getattr(settings, "send_temperature", False) or temperature is not None:
            selected_temperature = settings.temperature if temperature is None else temperature
            if (
                isinstance(selected_temperature, bool)
                or not isinstance(selected_temperature, (int, float))
                or not math.isfinite(selected_temperature)
                or not 0 <= selected_temperature <= 1
            ):
                raise self._error("llm_invalid_request")
            payload["temperature"] = selected_temperature
        if settings.provider == "anthropic":
            payload.update(system=system, messages=messages)
        else:
            payload["messages"] = [{"role": "system", "content": system}, *messages]
        return payload

    @staticmethod
    def _token_count(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
            raise ValueError("invalid usage")
        return value

    def _usage(self, result: dict[str, Any]) -> tuple[int | None, int | None]:
        usage = result.get("usage")
        if usage is None:
            return None, None
        if not isinstance(usage, dict):
            raise ValueError("invalid usage")
        if self.settings.provider == "anthropic":
            input_tokens = self._token_count(usage.get("input_tokens"))
            output_tokens = self._token_count(usage.get("output_tokens"))
            cached_write = self._token_count(usage.get("cache_creation_input_tokens", 0))
            cached_read = self._token_count(usage.get("cache_read_input_tokens", 0))
            if input_tokens is not None and cached_write is not None and cached_read is not None:
                input_tokens += cached_write + cached_read
            else:
                input_tokens = None
            return input_tokens, output_tokens
        # prompt_tokens includes cached prompt tokens for OpenAI.
        return self._token_count(usage.get("prompt_tokens")), self._token_count(
            usage.get("completion_tokens")
        )

    def _text(self, result: dict[str, Any], *, legacy: bool) -> tuple[str, str]:
        if self.settings.provider == "anthropic":
            finish = result.get("stop_reason", "end_turn" if legacy else None)
            blocks = result["content"]
            if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
                raise ValueError("invalid response")
            if any(block.get("type") == "tool_use" for block in blocks):
                raise self._error("llm_unsupported_response")
            if any(block.get("type") not in {"text", "thinking", "redacted_thinking"} for block in blocks):
                raise ValueError("invalid response")
            texts = [block["text"] for block in blocks if block.get("type") == "text"]
            if any(not isinstance(text, str) for text in texts):
                raise ValueError("invalid response")
            text = "".join(texts)
            if finish in {"max_tokens", "model_context_window_exceeded"}:
                raise self._error("llm_truncated_response")
            if finish == "refusal":
                raise self._error("llm_refused")
            if finish in {"tool_use", "pause_turn"}:
                raise self._error("llm_unsupported_response")
            if finish not in {"end_turn", "stop_sequence"}:
                raise ValueError("invalid response")
        else:
            choices = result["choices"]
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError("invalid response")
            finish = choices[0].get("finish_reason", "stop" if legacy else None)
            message = choices[0]["message"]
            if not isinstance(message, dict):
                raise ValueError("invalid response")
            if message.get("refusal"):
                raise self._error("llm_refused")
            if message.get("tool_calls") or message.get("function_call"):
                raise self._error("llm_unsupported_response")
            if finish == "length":
                raise self._error("llm_truncated_response")
            if finish == "content_filter":
                raise self._error("llm_refused")
            if finish in {"tool_calls", "function_call"}:
                raise self._error("llm_unsupported_response")
            if finish != "stop":
                raise ValueError("invalid response")
            text = message["content"]
        if not isinstance(text, str):
            raise ValueError("invalid response")
        if not text.strip():
            raise self._error("llm_empty_response")
        return text.strip(), finish

    def _parse(self, result: Any, *, legacy: bool, latency_ms: int) -> ModelResult:
        input_tokens = output_tokens = None
        try:
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            input_tokens, output_tokens = self._usage(result)
            text, finish = self._text(result, legacy=legacy)
            return ModelResult(text, input_tokens, output_tokens, finish, latency_ms)
        except LLMRequestError as exc:
            raise self._error(exc.code, input_tokens=input_tokens, output_tokens=output_tokens) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._error(
                "llm_invalid_response", input_tokens=input_tokens, output_tokens=output_tokens
            ) from None

    async def _request(self, payload: dict[str, Any], *, legacy: bool) -> ModelResult:
        settings = self.settings
        url = self._endpoint()
        key = settings.api_key.get_secret_value() if settings.api_key else ""
        headers = (
            {"x-api-key": key, "anthropic-version": "2023-06-01"}
            if settings.provider == "anthropic"
            else {"Authorization": "Bearer " + key}
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=settings.timeout_seconds,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client:
                    if legacy:
                        response = await client.post(url, headers=headers, json=payload)
                        response.raise_for_status()
                        if len(response.content) > self.max_response_bytes:
                            raise self._error("llm_response_too_large")
                        result = response.json()
                    else:
                        async with client.stream("POST", url, headers=headers, json=payload) as response:
                            if not 200 <= response.status_code < 300:
                                # Avoid reading potentially private error bodies.
                                raise self._error(f"llm_http_{response.status_code}")
                            length = response.headers.get("content-length")
                            if length and int(length) > self.max_response_bytes:
                                raise self._error("llm_response_too_large")
                            body = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=16_384):
                                if len(body) + len(chunk) > self.max_response_bytes:
                                    raise self._error("llm_response_too_large")
                                body.extend(chunk)
                            result = json.loads(body)
            return self._parse(result, legacy=legacy, latency_ms=round((time.monotonic() - started) * 1000))
        except LLMRequestError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise self._error("llm_timeout") from None
        except httpx.HTTPStatusError as exc:
            raise self._error(f"llm_http_{exc.response.status_code}") from None
        except httpx.RequestError:
            raise self._error("llm_connection_failed") from None
        except httpx.InvalidURL:
            raise self._error("llm_invalid_endpoint") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._error("llm_invalid_response") from None

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.failure = None
        if self.settings.model_mode != "live":
            raise self._error("llm_not_live")
        payload = self._payload(
            system=system,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            legacy=False,
        )
        return await self._request(payload, legacy=False)

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        if self.failure is not None:
            raise LLMRequestError(self.failure)
        payload = self._payload(
            system=system,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            legacy=True,
        )
        return (await self._request(payload, legacy=True)).text


class AnthropicLLMClient(HTTPModelClient):
    """Compatibility name for the original session client."""


class OpenAILLMClient(HTTPModelClient):
    pass


def endpoint_fingerprint(settings: Settings) -> str:
    """Hash the normalized actual endpoint without exposing its base URL."""
    endpoint = HTTPModelClient(settings)._endpoint()
    try:
        normalized = str(httpx.URL(endpoint))
    except httpx.InvalidURL:
        raise LLMRequestError("llm_invalid_endpoint") from None
    return sha256(normalized.encode("utf-8")).hexdigest()


def build_llm_client(settings: Settings | None = None) -> HTTPModelClient | None:
    settings = settings or load_settings()
    if settings.model_mode == "mock":
        return None
    settings.check_live_configuration()
    client_type = AnthropicLLMClient if settings.provider == "anthropic" else OpenAILLMClient
    return client_type(settings)


__all__ = [
    "AnthropicLLMClient",
    "OpenAILLMClient",
    "HTTPModelClient",
    "LLMRequestError",
    "ModelResult",
    "endpoint_fingerprint",
    "build_llm_client",
]
