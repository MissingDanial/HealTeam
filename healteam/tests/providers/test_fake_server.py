from __future__ import annotations

import json

import httpx
import pytest
from app.services.llm import HTTPModelClient, LLMRequestError, endpoint_fingerprint
from app.settings import Settings
from pydantic import SecretStr

from .fake_server import fake_provider


def settings(provider="openai", **changes):
    return Settings(
        model_mode="live",
        provider=provider,
        model="local-fixture-model",
        api_key=SecretStr("test-only-secret"),
        **changes,
    )


def prompt(task="family", actor="father"):
    return {
        "system": "Local fixture only",
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "actor": actor,
                        "events": [
                            {
                                "type": "utterance",
                                "seq": 12,
                                "actor": "trainee",
                                "content": "private user content must not be retained",
                            }
                        ],
                    }
                ),
            }
        ],
    }


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_local_fixture_speaks_both_real_http_protocols_without_retaining_private_data(provider):
    with fake_provider() as (state, base_url):
        client = HTTPModelClient(settings(provider, base_url=base_url))
        reply = await client.complete(**prompt())
        assert json.loads(reply.text)["actor"] == "father"
        assert (reply.input_tokens, reply.output_tokens) == (11, 7)
        async with httpx.AsyncClient(trust_env=False) as inspect:
            health = (await inspect.get(base_url.removesuffix("/v1") + "/fixture/health")).json()
        assert health["calls"] == state.calls == 1
        assert health["summaries"] == [
            {"provider": provider, "task": "family", "actor": "father", "purpose": "family"}
        ]
        assert "private user content" not in json.dumps(health)
        assert "test-only-secret" not in json.dumps(health)


async def test_fixture_control_faults_and_evidence_recovery_over_http():
    with fake_provider() as (state, base_url):
        client = HTTPModelClient(settings(base_url=base_url))
        async with httpx.AsyncClient(trust_env=False) as control:
            url = base_url.removesuffix("/v1") + "/fixture/control"
            assert (await control.post(url, json={"mode": "auth_error"})).status_code == 200
            with pytest.raises(LLMRequestError, match="llm_http_401"):
                await client.complete(**prompt())
            await control.post(url, json={"mode": "bad_evidence", "reset_calls": True})
            review = json.loads((await client.complete(**prompt("review"))).text)
            assert review["observations"][0]["evidence_seq"] == [999999]
            await control.post(url, json={"mode": "normal"})
            review = json.loads((await client.complete(**prompt("review"))).text)
            assert review["observations"][0]["evidence_seq"] == [12]
        assert state.calls == 2


def test_endpoint_fingerprint_normalizes_equivalent_urls_and_changes_with_target():
    first = endpoint_fingerprint(settings(base_url="https://GATEWAY.example:443/prefix/v1/"))
    equivalent = endpoint_fingerprint(settings(base_url="https://gateway.example/prefix/v1"))
    changed = endpoint_fingerprint(settings(base_url="https://other.example/prefix/v1"))
    assert first == equivalent and first != changed
    assert len(first) == 64 and "gateway" not in first


def test_endpoint_fingerprint_does_not_call_network_or_depend_on_key():
    first = settings(base_url="https://gateway.example/v1")
    rotated = first.model_copy(update={"api_key": SecretStr("different-test-key")})
    assert endpoint_fingerprint(first) == endpoint_fingerprint(rotated)


def test_deepseek_default_endpoint_fingerprint_matches_explicit_canonical_endpoint():
    assert endpoint_fingerprint(settings("deepseek")) == endpoint_fingerprint(
        settings("deepseek", base_url="https://api.deepseek.com/v1/")
    )
