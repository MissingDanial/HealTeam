"""Provider wire-format tests with no external requests or credentials."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from app.services.llm import HTTPModelClient, LLMRequestError, ModelResult, build_llm_client
from app.settings import Settings
from pydantic import SecretStr

SECRET = "test-only-private-credential"
CONTENT = {"system": "Stay in role", "messages": [{"role": "user", "content": "Tell me more"}]}


def settings(provider="openai", **changes):
    return Settings(
        model_mode="live", provider=provider, api_key=SecretStr(SECRET), model="test-model", **changes
    )


def success(provider="openai", *, text="A complete reply", usage=True):
    if provider == "anthropic":
        response = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
        if usage:
            response["usage"] = {"input_tokens": 12, "output_tokens": 7}
        return response
    response = {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
    if usage:
        response["usage"] = {"prompt_tokens": 12, "completion_tokens": 7}
    return response


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_complete_sends_provider_protocol_and_returns_typed_usage(provider):
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, json=success(provider))

    client = HTTPModelClient(settings(provider, max_tokens=64), transport=httpx.MockTransport(handle))
    result = await client.complete(**CONTENT, max_tokens=2048)
    assert isinstance(result, ModelResult)
    assert result.text == "A complete reply"
    assert (result.input_tokens, result.output_tokens) == (12, 7)
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    request = seen[0]
    payload = json.loads(request.content)
    assert payload["model"] == "test-model" and payload["stream"] is False
    assert "temperature" not in payload
    if provider == "openai":
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + SECRET
        assert payload["max_completion_tokens"] == 64 and "max_tokens" not in payload
        assert payload["messages"][0] == {"role": "system", "content": CONTENT["system"]}
    else:
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == SECRET
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert payload["max_tokens"] == 64
        assert payload["system"] == CONTENT["system"]


@pytest.mark.parametrize(
    ("provider", "base", "expected"),
    [
        ("anthropic", "http://localhost:9000", "http://localhost:9000/v1/messages"),
        ("anthropic", "http://localhost:9000/v1/", "http://localhost:9000/v1/messages"),
        ("anthropic", "https://gateway.example/prefix/v1", "https://gateway.example/prefix/v1/messages"),
        (
            "openai",
            "https://gateway.example/prefix/v1/",
            "https://gateway.example/prefix/v1/chat/completions",
        ),
    ],
)
async def test_custom_base_preserves_prefix_and_does_not_duplicate_v1(provider, base, expected):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=success(provider))

    await HTTPModelClient(settings(provider, base_url=base), transport=httpx.MockTransport(handle)).complete(
        **CONTENT
    )
    assert str(requests[0].url) == expected


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500, 503])
async def test_http_failures_do_not_redirect_retry_or_expose_provider_details(status, caplog):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://private.example/secret"}, text=SECRET)

    client = HTTPModelClient(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code == f"llm_http_{status}"
    assert len(requests) == 1
    assert SECRET not in str(caught.value) + repr(caught.value) + caplog.text
    assert "private.example" not in str(caught.value)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_missing_usage_is_unknown_not_zero(provider):
    client = HTTPModelClient(
        settings(provider),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=success(provider, usage=False))
        ),
    )
    result = await client.complete(**CONTENT)
    assert result.input_tokens is None and result.output_tokens is None


async def test_anthropic_usage_includes_cache_read_and_creation():
    response = success("anthropic")
    response["usage"].update(cache_creation_input_tokens=100, cache_read_input_tokens=200)
    client = HTTPModelClient(
        settings("anthropic"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
    )
    result = await client.complete(**CONTENT)
    assert result.input_tokens == 312 and result.output_tokens == 7


async def test_openai_cached_prompt_tokens_are_not_counted_twice():
    response = success()
    response["usage"]["prompt_tokens_details"] = {"cached_tokens": 8}
    client = HTTPModelClient(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    )
    assert (await client.complete(**CONTENT)).input_tokens == 12


@pytest.mark.parametrize(
    ("provider", "finish", "code"),
    [
        ("anthropic", "max_tokens", "llm_truncated_response"),
        ("anthropic", "model_context_window_exceeded", "llm_truncated_response"),
        ("anthropic", "refusal", "llm_refused"),
        ("anthropic", "tool_use", "llm_unsupported_response"),
        ("anthropic", "pause_turn", "llm_unsupported_response"),
        ("openai", "length", "llm_truncated_response"),
        ("openai", "content_filter", "llm_refused"),
        ("openai", "tool_calls", "llm_unsupported_response"),
        ("openai", "unknown", "llm_invalid_response"),
    ],
)
async def test_incomplete_or_refused_response_fails_with_usage_preserved(provider, finish, code):
    response = success(provider, text=SECRET)
    if provider == "anthropic":
        response["stop_reason"] = finish
    else:
        response["choices"][0]["finish_reason"] = finish
    client = HTTPModelClient(
        settings(provider), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    )
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code == code
    assert caught.value.input_tokens == 12 and caught.value.output_tokens == 7
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "ok"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": 12}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": " "}}]},
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": True},
        },
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": -1},
        },
    ],
)
async def test_malformed_responses_never_become_successful_text(response):
    client = HTTPModelClient(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    )
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code in {"llm_invalid_response", "llm_empty_response"}


async def test_bad_text_shape_still_preserves_valid_usage():
    response = success()
    response["choices"][0]["message"]["content"] = 12
    client = HTTPModelClient(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    )
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code == "llm_invalid_response"
    assert (caught.value.input_tokens, caught.value.output_tokens) == (12, 7)


@pytest.mark.parametrize("kind", ["invalid_json", "content_length", "streamed_length"])
async def test_invalid_or_oversized_body_is_rejected(kind):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 40
            yield b"y" * 40

    def handle(request):
        if kind == "invalid_json":
            return httpx.Response(200, content=b"not json")
        if kind == "content_length":
            return httpx.Response(200, headers={"content-length": "1000"}, content=b"{}")
        return httpx.Response(200, stream=Chunks())

    client = HTTPModelClient(settings(), transport=httpx.MockTransport(handle), max_response_bytes=64)
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code == (
        "llm_invalid_response" if kind == "invalid_json" else "llm_response_too_large"
    )


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_errors_have_safe_codes(failure):
    def handle(request):
        raise failure(SECRET, request=request)

    client = HTTPModelClient(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code == ("llm_timeout" if failure is httpx.ReadTimeout else "llm_connection_failed")
    assert SECRET not in str(caught.value)


async def test_wall_clock_deadline_bounds_a_slow_stream():
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.2)
            yield b"{}"

    client = HTTPModelClient(
        settings(timeout_seconds=0.01),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Slow())),
    )
    with pytest.raises(LLMRequestError, match="llm_timeout"):
        await client.complete(**CONTENT)


async def test_a_failed_complete_call_does_not_disable_later_calls():
    attempts = 0

    def handle(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(429) if attempts == 1 else httpx.Response(200, json=success())

    client = HTTPModelClient(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(LLMRequestError, match="llm_http_429"):
        await client.complete(**CONTENT)
    assert (await client.complete(**CONTENT)).text == "A complete reply"
    assert client.failure is None and attempts == 2


async def test_mock_client_cannot_make_a_complete_request():
    def forbidden(request):
        raise AssertionError("mock must not call any provider")

    assert build_llm_client(Settings()) is None
    client = HTTPModelClient(Settings(), transport=httpx.MockTransport(forbidden))
    with pytest.raises(LLMRequestError, match="llm_not_live"):
        await client.complete(**CONTENT)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://gateway.example/v1/messages",
        "https://gateway.example/v1/chat/completions",
        "https://gateway.example/v1/responses",
        "https://gateway.example:bad/v1",
    ],
)
async def test_operation_urls_and_malformed_ports_are_rejected_before_request(endpoint):
    def forbidden(request):
        raise AssertionError("invalid base must not call a provider")

    client = HTTPModelClient(settings(base_url=endpoint), transport=httpx.MockTransport(forbidden))
    with pytest.raises(LLMRequestError, match="llm_invalid_endpoint"):
        await client.complete(**CONTENT)


async def test_explicit_temperature_can_be_sent_when_the_model_supports_it():
    payloads = []

    def handle(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=success())

    client = HTTPModelClient(settings(), transport=httpx.MockTransport(handle))
    await client.complete(**CONTENT, temperature=0.2)
    assert payloads[0]["temperature"] == 0.2


async def test_deepseek_uses_its_token_field_and_disables_thinking_without_exposing_reasoning():
    requests = []
    response = success()
    response["choices"][0]["message"]["reasoning_content"] = "private reasoning must not be shown"
    response["usage"].update(prompt_cache_hit_tokens=9, prompt_cache_miss_tokens=3)

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    client = HTTPModelClient(settings("deepseek", max_tokens=64), transport=httpx.MockTransport(handle))
    result = await client.complete(**CONTENT, max_tokens=1024)
    request = requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer " + SECRET
    assert payload["max_tokens"] == 64 and "max_completion_tokens" not in payload
    assert payload["thinking"] == {"type": "disabled"}
    assert "temperature" not in payload
    assert result.text == "A complete reply"
    assert "private reasoning" not in repr(result)
    assert (result.input_tokens, result.output_tokens) == (12, 7)


@pytest.mark.parametrize(
    "finish,code", [("length", "llm_truncated_response"), ("content_filter", "llm_refused")]
)
async def test_deepseek_stop_errors_preserve_usage(finish, code):
    response = success()
    response["choices"][0]["finish_reason"] = finish
    client = HTTPModelClient(
        settings("deepseek"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
    )
    with pytest.raises(LLMRequestError) as caught:
        await client.complete(**CONTENT)
    assert caught.value.code == code
    assert (caught.value.input_tokens, caught.value.output_tokens) == (12, 7)
