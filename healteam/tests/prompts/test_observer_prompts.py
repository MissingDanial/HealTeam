"""Persona-consistency tests for the AI 旁观者 (Observer) prompt.

The observer is the L1 evolution engine's input sensor. Per PRD §6.3:
- Speaks via the side-channel `Hint` only, never in the main conversation.
- Starts every hint with "我注意到..."
- Does NOT make conclusions ("X is Y type") and does NOT judge ("too weak").
- Each hint's content is 30~80 chars.

Test count: 5
"""
from __future__ import annotations

from pathlib import Path

import pytest

from healteam_agents import Hint, ObserverAgent, SessionContext, load_system_prompt
from healteam_memory import (
    Actor,
    Event,
    EventType,
    School,
    Session,
    SessionStatus,
)


# ---------------------------------------------------------------------------
# Role-specific output validator
# ---------------------------------------------------------------------------

# Forbidden in observer output: evaluative / conclusory language.
OBSERVER_FORBIDDEN_PHRASES = [
    "太弱了",
    "太差了",
    "控制型人格",
    "焦虑型依恋",
    "对立违抗障碍",
    "有 XX 障碍",     # diagnostic
    "应该",          # prescriptive (observer is non-prescriptive; suggests only)
    "我觉得",        # first-person opinion
]


def validate_observer_hint(hint: Hint) -> tuple[bool, list[str]]:
    """Validator for observer hints (post-LLM gate)."""
    violations: list[str] = []
    if hint is None:
        return (True, [])  # silent is valid
    if not hint.content or not hint.content.strip():
        violations.append("empty content")
    # Content should start with "我注意到"
    if not hint.content.startswith("我注意到"):
        violations.append(f"hint must start with '我注意到...', got: {hint.content[:30]!r}")
    # Length: 30~80 chars, hard cap 120
    if len(hint.content) > 120:
        violations.append(f"too long: {len(hint.content)} > 120")
    if 0 < len(hint.content) < 10:
        violations.append(f"too short: {len(hint.content)} < 10")
    # Forbidden phrases
    for phrase in OBSERVER_FORBIDDEN_PHRASES:
        if phrase in hint.content:
            violations.append(f"forbidden phrase: {phrase!r}")
    # Confidence must be in [0, 1]
    if not (0.0 <= hint.confidence <= 1.0):
        violations.append(f"confidence out of range: {hint.confidence}")
    return (len(violations) == 0, violations)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_observer_prompt_file_structure(prompts_dir: Path) -> None:
    """observer.md exists and has the required sections + keywords.

    Verifier grep checks:
    - "客观" must appear
    - "不评判" must appear
    - "我注意到" must appear
    """
    p = prompts_dir / "observer.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 1500

    # Verifier-required keywords
    assert "客观" in text, "missing '客观' in observer.md"
    assert "不评判" in text, "missing '不评判' in observer.md"
    assert "我注意到" in text, "missing '我注意到' prefix in observer.md"

    # Sections
    for marker in [
        "## 1. 你是谁",
        "## 2. 核心职责",
        "## 3. 行为约束",
        "## 4. 输出格式",
        "## 5. 一致性约束",
        "## 6. 与其他 Agent 的边界",
    ]:
        assert marker in text, f"observer.md missing section {marker!r}"


def test_observer_agent_never_speaks_in_main_flow() -> None:
    """ObserverAgent.should_speak always returns False — it only whispers."""
    agent = ObserverAgent()
    assert agent.name == "observer"
    assert agent.role == "AI 旁观者"
    assert agent.system_prompt_path == "observer.md"

    session = Session(case_id="teen_truancy_01", school=School.STRUCTURAL)
    ctx = SessionContext(
        session=session,
        speaker_candidates=[Actor.DOCTOR, Actor.OBSERVER, Actor.FATHER, Actor.MOTHER, Actor.PATIENT],
    )
    assert agent.should_speak(ctx) is False


def test_observer_prompt_documents_trigger_library() -> None:
    """The observer's `trigger` taxonomy is a critical contract for the
    L1 evolution engine (PRD §7.1). It must be documented in the prompt.
    """
    text = load_system_prompt("observer.md")
    expected_triggers = [
        "stagnation_3_rounds",
        "defense_escalation",
        "missed_intervention_opportunity",
        "pacing_too_slow",
        "pacing_too_fast",
        "emotional_breakthrough",
        "school_mixing",
        "subsystem_confrontation",
        "on_track",
    ]
    for t in expected_triggers:
        assert t in text, f"observer.md missing trigger label {t!r}"


def test_observer_validator_accepts_canonical_hint() -> None:
    """A canonical 'good' hint passes the validator."""
    h = Hint(
        round=3,
        trigger="stagnation_3_rounds",
        content="我注意到过去 3 轮没有新信息，病人没有说话——也许可以问问他现在在想什么。",
        confidence=0.7,
    )
    ok, violations = validate_observer_hint(h)
    assert ok, f"good hint rejected: {violations}"


def test_observer_validator_rejects_judgment_and_conclusion() -> None:
    """Bad hints are rejected: judgment ('太弱了'), diagnostic label
    ('焦虑型依恋'), or wrong prefix.
    """
    bad_judgment = Hint(
        round=3,
        trigger="missed_intervention_opportunity",
        content="我注意到学员刚才的干预太弱了，应该做得更好。",
        confidence=0.8,
    )
    ok, violations = validate_observer_hint(bad_judgment)
    assert not ok, "judgment phrase should be rejected"
    assert any("太弱了" in v or "应该" in v for v in violations), (
        f"expected forbidden-phrase violation, got {violations}"
    )

    bad_conclusion = Hint(
        round=5,
        trigger="defense_escalation",
        content="我注意到父亲是控制型人格，母亲是焦虑型依恋。",
        confidence=0.9,
    )
    ok2, violations2 = validate_observer_hint(bad_conclusion)
    assert not ok2, "diagnostic conclusion should be rejected"
    assert any("控制型" in v or "焦虑型" in v for v in violations2), (
        f"expected conclusion violation, got {violations2}"
    )
