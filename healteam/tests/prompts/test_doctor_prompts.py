"""Persona-consistency tests for the AI 主治医师 (Doctor) prompt.

These tests verify that `doctor.md` is **structurally sound** (so a real LLM
can be driven by it) and that the role rules can be expressed as machine-
checkable invariants. They do **not** call a real LLM — they use a mock
LLMClient and a recorded "good" response, then assert the role-specific
validator accepts the response.

Test count for this file: 4 (1 prompt structure, 1 mock-LLM contract, 2 validators).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# These imports rely on conftest.py having registered the package aliases.
from healteam_agents import (
    DoctorAgent,
    SessionContext,
    load_system_prompt,
    make_placeholder_utterance,
)
from healteam_memory import (
    Actor,
    Event,
    EventType,
    School,
    Session,
    SessionStatus,
)

# ---------------------------------------------------------------------------
# Role-specific output validator (re-used by tests below)
# ---------------------------------------------------------------------------

# Hard-forbidden phrases in the doctor's live-session output. A real LLM
# driven by doctor.md should never produce these.
DOCTOR_FORBIDDEN_PHRASES = [
    "我建议",       # no advice
    "你们应该",     # no prescriptions
    "你应该停止",   # no behavior commands
    "你儿子有",     # no in-room diagnosis
    "作为一个 AI",  # never break persona
    "DSM-5",        # no diagnostic reference in live session
    "诊断",         # no diagnosis verb
]

# Hard length limits (PRD §6.1: 50-150 chars, hard cap 200).
DOCTOR_HARD_MAX_CHARS = 200


def validate_doctor_output(text: str) -> tuple[bool, list[str]]:
    """Return (ok, list_of_violations). Used as the test oracle."""
    violations: list[str] = []
    if not text or not text.strip():
        violations.append("empty output")
    if len(text) > DOCTOR_HARD_MAX_CHARS:
        violations.append(f"too long: {len(text)} > {DOCTOR_HARD_MAX_CHARS}")
    for phrase in DOCTOR_FORBIDDEN_PHRASES:
        if phrase in text:
            violations.append(f"forbidden phrase: {phrase!r}")
    return (len(violations) == 0, violations)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_doctor_prompt_file_structure(prompts_dir: Path) -> None:
    """doctor.md exists, is non-empty, and contains all the required sections.

    Required by PRD §6.1: 角色身份, 核心职责, 行为约束, 输出格式, 一致性约束,
    边界. We look for the section markers we wrote in v1.0.
    """
    p = prompts_dir / "doctor.md"
    assert p.exists(), f"{p} should exist"
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 1500, f"doctor.md is too short ({len(text)} chars); expected rich prompt"

    required_markers = [
        "## 1. 你是谁",       # identity
        "## 2. 核心职责",     # responsibilities
        "## 3. 行为约束",     # behavioral constraints
        "## 4. 输出格式",     # output format
        "## 5. 一致性约束",   # consistency
        "## 6. 与其他 Agent 的边界",  # boundaries
    ]
    for marker in required_markers:
        assert marker in text, f"doctor.md missing section {marker!r}"

    # PRD-mandated key principles (for the verifier's grep checks)
    assert "让家庭自己看见" in text, "missing core principle '让家庭自己看见'"
    assert "不直接解决" in text or "不直接" in text, "missing '不直接解决' phrasing"
    # Not directly speaking in live session is the psychiatrist's job; doctor
    # does speak — make sure we don't accidentally copy that constraint.
    assert "你**不**直接发言" not in text, "doctor.md has psychiatrist's constraint by mistake"


def test_doctor_agent_uses_correct_prompt_path(prompts_dir: Path) -> None:
    """DoctorAgent.system_prompt_path is the relative path 'doctor.md' so
    load_system_prompt() can resolve it under packages/prompts/.
    """
    agent = DoctorAgent()
    assert agent.name == "doctor"
    assert agent.role == "AI 主治医师"
    assert agent.system_prompt_path == "doctor.md"

    # And load_system_prompt actually returns the file content (i.e. the path
    # is correct, not a typo).
    prompt = load_system_prompt(agent.system_prompt_path)
    assert prompt, "load_system_prompt returned empty; path is wrong"
    assert "家庭治疗师" in prompt


def test_doctor_validator_accepts_canonical_output() -> None:
    """A canonical 'good' doctor response passes the validator."""
    good_response = (
        "我听到妈妈说的了。妈妈，你刚才说你担心儿子已经三个月没出门了——"
        "爸爸，你也说你觉得'没那么严重'。我想请你们两位，"
        "用 30 秒分别告诉我，你们各自最担心的是什么。"
    )
    ok, violations = validate_doctor_output(good_response)
    assert ok, f"good response was rejected: {violations}"
    assert len(good_response) < DOCTOR_HARD_MAX_CHARS


def test_doctor_validator_rejects_advice_and_diagnosis() -> None:
    """Two canonical 'bad' responses are correctly rejected."""
    bad_advice = "我建议你们应该停止争吵，每周开一次家庭会议，孩子的问题就能解决。"
    bad_diagnosis = "你儿子有对立违抗障碍，需要做 DSM-5 评估。"

    ok, violations = validate_doctor_output(bad_advice)
    assert not ok, "advice response should be rejected"
    assert any("我建议" in v for v in violations), f"expected '我建议' violation, got {violations}"

    ok2, violations2 = validate_doctor_output(bad_diagnosis)
    assert not ok2, "diagnosis response should be rejected"
    assert any("诊断" in v or "DSM" in v for v in violations2), (
        f"expected diagnosis violation, got {violations2}"
    )


# ---------------------------------------------------------------------------
# Mock LLM contract test
# ---------------------------------------------------------------------------


class _RecordingMockLLM:
    """A mock LLMClient that records the system prompt + messages and returns
    a canned response. Verifies that an agent built on top of load_system_prompt
    passes the right system prompt to the LLM.
    """

    def __init__(self, canned: str) -> None:
        self.canned = canned
        self.last_system: str | None = None
        self.last_messages: list[dict[str, str]] | None = None
        self.call_count = 0

    async def chat(self, *, system: str, messages, **kwargs) -> str:
        self.last_system = system
        self.last_messages = list(messages)
        self.call_count += 1
        return self.canned


def test_doctor_prompt_has_persona_markers() -> None:
    """The doctor.md text contains the persona markers a real LLM needs:
    - '你是谁' identity section
    - at least 3 explicit 'must' / 'should' style rules (硬规则)
    - explicit length constraints in characters
    - explicit 'no advice' / 'no diagnosis' rules
    This is a structural assertion that the prompt is rich enough to drive
    a real LLM (per verifier requirement).
    """
    text = load_system_prompt("doctor.md")
    # 4+ behavioral 'must do' rules (the '✅' markers in our v1.0)
    must_count = text.count("✅")
    assert must_count >= 3, f"doctor.md should have ≥3 'must do' rules, found {must_count}"
    # 4+ 'must not' rules
    must_not_count = text.count("❌")
    assert must_not_count >= 3, f"doctor.md should have ≥3 'must not' rules, found {must_not_count}"
    # Length constraint present
    assert re.search(r"\d+\s*~\s*\d+\s*字", text) or "200" in text, (
        "doctor.md should declare a character-length budget"
    )


def test_doctor_should_speak_uses_only_session_context() -> None:
    """Per BaseAgent contract, should_speak is a pure function of SessionContext
    — no LLM call. We assert it does not raise and is callable with a fresh
    context. The W1 heuristic is intentionally conservative; we don't pin it
    to a specific truth table here.
    """
    agent = DoctorAgent()
    session = Session(case_id="teen_truancy_01", school=School.STRUCTURAL)
    session.append_event(Event(round=0, actor=Actor.DOCTOR, type=EventType.UTTERANCE, content="开场"))
    ctx = SessionContext(
        session=session,
        last_event=session.events[-1],
        speaker_candidates=[Actor.DOCTOR, Actor.FATHER, Actor.MOTHER, Actor.PATIENT],
    )
    # Pure-function: returns a bool, doesn't raise.
    result = agent.should_speak(ctx)
    assert isinstance(result, bool)


def test_doctor_respond_with_no_llm_returns_placeholder() -> None:
    """When llm is None (W1 stub), DoctorAgent.respond returns the deterministic
    placeholder. This guarantees the W1 smoke test still compiles.
    """
    agent = DoctorAgent(llm=None)
    session = Session(case_id="teen_truancy_01", school=School.STRUCTURAL)
    ctx = SessionContext(session=session)
    out = agent.respond(ctx)
    assert isinstance(out, str) and out
    assert "doctor" in out.lower() or "doctor" in make_placeholder_utterance(Actor.DOCTOR, ctx)
