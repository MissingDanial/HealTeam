"""Real HTTP, process kill/restart and event-stream reliability acceptance."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]


def create_command(**changes):
    return {
        "client_request_id": str(uuid4()),
        "case_id": "teen-truancy-01",
        "school": "satir",
        "mode": "training",
        "max_rounds": 1,
        **changes,
    }


def message(turn=1):
    return {"client_message_id": str(uuid4()), "expected_turn": turn, "content": "请爸爸先说说。"}


def wait_for(client, session_id, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200, response.text
        view = response.json()
        if predicate(view):
            return view
        time.sleep(0.02)
    raise AssertionError(f"Expected state not reached: {view}")


@contextmanager
def server(tmp_path, *, delay=0.005, mode="normal"):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = uuid4().hex
    pid_file = tmp_path / f"server-{token}.pid.json"
    log_file = tmp_path / f"server-{token}.log"
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("HEALTEAM_", "ANTHROPIC_", "OPENAI_", "LLM_")):
            environment.pop(name)
    environment.update(
        HEALTEAM_ROOT=str(ROOT),
        HEALTEAM_MODEL_MODE="mock",
        HEALTEAM_DATABASE_URL=str(tmp_path / "training.sqlite3"),
        HEALTEAM_OPERATION_TIMEOUT_SECONDS="30",
        HEALTEAM_STOP_TIMEOUT_SECONDS="0.1",
        HEALTEAM_STORAGE_BUSY_TIMEOUT_SECONDS="0.5",
        PYTHONUTF8="1",
    )
    with log_file.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("runtime_server.py")),
                str(port),
                str(delay),
                mode,
                str(pid_file),
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False)

        def kill_owned_server():
            # On Windows the venv launcher may own a separate Python child.
            # The private handshake records only the process started above.
            if pid_file.exists():
                pid = json.loads(pid_file.read_text(encoding="utf-8"))["pid"]
                try:
                    os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    if process.poll() is None:
                        raise
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(log_file.read_text(encoding="utf-8"))
                try:
                    if client.get("/readyz").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError(f"Server not ready: {log_file.read_text(encoding='utf-8')}")
            yield client, kill_owned_server
        finally:
            client.close()
            if process.poll() is None:
                kill_owned_server()


def spoken(view):
    return [event for event in view["events"] if event["type"] == "utterance"]


def test_force_kill_during_response_preserves_input_and_does_not_replay_partial_turn(tmp_path):
    with server(tmp_path, delay=1) as (client, kill):
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        command = message()
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=command).status_code == 202
        before = client.get(f"/api/v1/sessions/{session_id}").json()
        assert before["state"] == "running"
        kill()
    with server(tmp_path) as (client, _kill):
        recovered = client.get(f"/api/v1/sessions/{session_id}").json()
        assert recovered["state"] == "interrupted" and recovered["completed_rounds"] == 0
        assert spoken(recovered) == spoken(before)
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=command).status_code == 202
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=message()).status_code == 409
        assert client.get(f"/api/v1/sessions/{session_id}").json()["events"] == recovered["events"]


def test_force_kill_while_waiting_keeps_the_session_usable(tmp_path):
    with server(tmp_path) as (client, kill):
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        kill()
    with server(tmp_path) as (client, _kill):
        assert client.get(f"/api/v1/sessions/{session_id}").json()["state"] == "awaiting_input"
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        wait_for(client, session_id, lambda view: view["state"] == "completed")


def test_force_kill_during_report_allows_retry_without_repeating_dialogue(tmp_path):
    with server(tmp_path, mode="blocked_report") as (client, kill):
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        before = wait_for(client, session_id, lambda view: view["phase"] == "report")
        kill()
    with server(tmp_path) as (client, _kill):
        recovered = client.get(f"/api/v1/sessions/{session_id}").json()
        assert recovered["state"] == "interrupted" and recovered["report_state"] == "failed"
        assert client.post(f"/api/v1/reports/{session_id}/retry").status_code == 202
        complete = wait_for(client, session_id, lambda view: view["state"] == "completed")
        assert spoken(complete) == spoken(before)
        assert len([event for event in complete["events"] if event["type"] == "report_ready"]) == 1
        assert complete["error_code"] is None


def test_disconnect_stream_does_not_stop_worker_and_cursor_replays_only_new_events(tmp_path):
    with server(tmp_path, delay=0.02) as (client, _kill):
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        cursor = 0
        with client.stream("GET", f"/api/v1/sessions/{session_id}/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("id: "):
                    cursor = int(line[4:])
                    break
        assert cursor > 0
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        complete = wait_for(client, session_id, lambda view: view["state"] == "completed")
        response = client.get(f"/api/v1/sessions/{session_id}/events", headers={"Last-Event-ID": str(cursor)})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events == [event for event in complete["events"] if event["seq"] > cursor]


def test_thirty_rounds_with_duplicate_request_bursts_commit_one_input_each(tmp_path):
    with server(tmp_path) as (client, _kill):
        session_id = client.post("/api/v1/sessions", json=create_command(max_rounds=30)).json()["session_id"]
        for turn in range(1, 31):
            command = message(turn)
            with ThreadPoolExecutor(max_workers=4) as executor:
                statuses = list(
                    executor.map(
                        lambda _index, payload=command: (
                            client.post(f"/api/v1/sessions/{session_id}/messages", json=payload).status_code
                        ),
                        range(4),
                    )
                )
            assert statuses == [202] * 4
            latest = wait_for(
                client, session_id, lambda view: view["state"] in {"awaiting_input", "completed"}
            )
            assert latest["completed_rounds"] == turn
        assert latest["state"] == "completed"
        assert len([event for event in spoken(latest) if event["source"] == "human"]) == 30
        sequence = [event["seq"] for event in latest["events"]]
        assert sequence == list(range(1, latest["last_event_seq"] + 1))
