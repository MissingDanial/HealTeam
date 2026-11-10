"""Local-only dual-protocol model fixture; it never calls external providers.

CLI: python tests/providers/fake_server.py --port 8011
Context: with fake_provider() as (state, base_url): ...
base_url includes /v1 and works for either configured provider.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODES = frozenset(
    {
        "normal",
        "auth_error",
        "rate_limit",
        "server_error",
        "invalid_json",
        "truncated",
        "wrong_actor",
        "bad_evidence",
        "bad_assessment",
        "bad_assessment_evidence",
        "wrong_milestone_actor",
    }
)
MAX_REQUEST_BYTES = 1_048_576
TASKS = frozenset({"family", "doctor", "review"})
ACTORS = frozenset({"father", "mother", "patient", "doctor"})


@dataclass
class FixtureState:
    mode: str = "normal"
    calls: int = 0
    summaries: list[dict[str, str]] = field(default_factory=list)
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def configure(self, mode: str, *, reset_calls: bool = False) -> None:
        if mode not in MODES:
            raise ValueError("invalid fixture mode")
        with self._guard:
            self.mode = mode
            if reset_calls:
                self.calls = 0
                self.summaries.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            return {
                "test_fixture": True,
                "mode": self.mode,
                "calls": self.calls,
                "summaries": [dict(item) for item in self.summaries],
            }

    def record(self, data: dict[str, Any], provider: str) -> str:
        task = data.get("task")
        actor = data.get("actor")
        purpose = data.get("purpose", task)
        with self._guard:
            self.calls += 1
            self.summaries.append(
                {
                    "provider": provider,
                    "task": task if isinstance(task, str) and task in TASKS else "unknown",
                    "actor": actor if isinstance(actor, str) and actor in ACTORS else "none",
                    "purpose": purpose if isinstance(purpose, str) and purpose in TASKS else "unknown",
                }
            )
            return self.mode


def _request_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ValueError("invalid model envelope")
    for message in reversed(payload["messages"]):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError("invalid fixture prompt")
            data = json.loads(content)
            if not isinstance(data, dict) or data.get("task") not in TASKS:
                raise ValueError("invalid fixture task")
            return data
    raise ValueError("missing user prompt")


def _model_text(data: dict[str, Any], mode: str) -> str:
    if mode == "invalid_json":
        return '{"not_valid_json":'
    if data["task"] == "review":
        evidence = next(
            (
                event["seq"]
                for event in data.get("events", [])
                if isinstance(event, dict)
                and event.get("type") == "utterance"
                and type(event.get("seq")) is int
            ),
            1,
        )
        if mode == "bad_evidence":
            evidence = 999999
        result = {
            "summary": "本地测试服务生成的AI辅助复盘，未经专家校准。",
            "observations": [
                {"text": "本地测试观察：已保存一次明确的干预与回应。", "evidence_seq": [evidence]}
            ],
            "next_steps": [
                {"text": "本地测试练习方向：下一轮继续听取家庭成员表达。", "evidence_seq": [evidence]}
            ],
        }
        rubric = data.get("rubric")
        if isinstance(rubric, dict):
            target_actor = "doctor" if data.get("mode") == "demo" else "trainee"
            intervention = next(
                (event["seq"] for event in data.get("events", []) if event.get("actor") == target_actor),
                None,
            )
            result["assessment"] = {
                "dimensions": [
                    {
                        "dimension_id": item["dimension_id"],
                        "rating": 1 if intervention is not None else None,
                        "rationale": "本地测试用分项观察，需人工复核，不是专业成绩。",
                        "evidence_seq": [intervention] if intervention is not None else [],
                    }
                    for item in rubric["dimensions"]
                ],
                "milestones": [
                    {
                        "milestone_id": item["milestone_id"],
                        "status": "insufficient_evidence",
                        "rationale": "本地测试窗口不足以判断病例目标。",
                        "evidence_seq": [],
                    }
                    for item in rubric["milestones"]
                ],
            }
            if mode == "bad_assessment":
                result["assessment"]["dimensions"][0]["rating"] = True
            if mode == "bad_assessment_evidence":
                result["assessment"]["dimensions"][0]["evidence_seq"] = [999999]
            if mode == "wrong_milestone_actor" and result["assessment"]["milestones"]:
                result["assessment"]["milestones"][0].update(
                    status="observed", evidence_seq=[intervention if intervention is not None else evidence]
                )
    else:
        actor = data.get("actor", "doctor" if data["task"] == "doctor" else "father")
        if not isinstance(actor, str) or actor not in ACTORS:
            raise ValueError("invalid fixture actor")
        if mode == "wrong_actor":
            actor = "mother" if actor != "mother" else "father"
        result = {"actor": actor, "content": f"我是本地测试角色 {actor}，我愿意先说说刚才听到的想法。"}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _handler(state: FixtureState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *args: object) -> None:
            # No request headers, model prompts or user content enter logs.
            pass

        def _send(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _body(self) -> Any:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST_BYTES:
                raise ValueError("invalid fixture body")
            return json.loads(self.rfile.read(size))

        def do_GET(self) -> None:
            if self.path == "/fixture/health":
                self._send(200, state.snapshot())
            else:
                self._send(404, {"error": "fixture_route_not_found"})

        def do_POST(self) -> None:
            try:
                payload = self._body()
                if self.path == "/fixture/control":
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(payload.get("mode"), str)
                        or payload["mode"] not in MODES
                        or not isinstance(payload.get("reset_calls", False), bool)
                    ):
                        raise ValueError("invalid fixture control")
                    state.configure(payload["mode"], reset_calls=payload.get("reset_calls", False))
                    self._send(200, state.snapshot())
                    return
                provider = {"/v1/messages": "anthropic", "/v1/chat/completions": "openai"}.get(self.path)
                if provider is None:
                    self._send(404, {"error": "fixture_route_not_found"})
                    return
                data = _request_data(payload)
                mode = state.record(data, provider)
                failures = {"auth_error": 401, "rate_limit": 429, "server_error": 500}
                if mode in failures:
                    self._send(failures[mode], {"error": {"type": "local_fixture_error"}})
                    return
                text = _model_text(data, mode)
                if provider == "anthropic":
                    response = {
                        "id": "msg_local_fixture",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                        "stop_reason": "max_tokens" if mode == "truncated" else "end_turn",
                        "usage": {"input_tokens": 11, "output_tokens": 7},
                    }
                else:
                    response = {
                        "id": "chatcmpl_local_fixture",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text},
                                "finish_reason": "length" if mode == "truncated" else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                    }
                self._send(200, response)
            except (ValueError, TypeError, KeyError):
                self.close_connection = True
                self._send(400, {"error": "invalid_fixture_request"})

    return Handler


@contextmanager
def fake_provider(*, mode: str = "normal", port: int = 0) -> Iterator[tuple[FixtureState, str]]:
    """Yield state and a /v1 base URL; bind only to the IPv4 loopback address."""
    if mode not in MODES:
        raise ValueError("invalid fixture mode")
    state = FixtureState(mode=mode)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(state))
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, name="local-model-fixture", daemon=True
    )
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with fake_provider(port=args.port) as (_state, base_url):
        print(
            json.dumps(
                {
                    "test_fixture": True,
                    "base_url": base_url,
                    "health_url": base_url.removesuffix("/v1") + "/fixture/health",
                }
            ),
            flush=True,
        )
        try:
            while True:
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
