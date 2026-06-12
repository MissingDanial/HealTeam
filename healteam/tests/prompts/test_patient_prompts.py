"""Persona-consistency tests for the AI 病人 (Patient) prompt.

The patient is a 16-year-old male with **avoidance / somatization /
regression** defense patterns. Per PRD §6.4:
- Speaks 5~30 chars (default); up to 60 when breaking through
- Hard cap 80 chars
- Uses short, oral, teen language ("……", "嗯", "不知道", "还行吧")
- Does NOT use professional psych vocabulary
- Slow-warming: 8~12 rounds before opening up

Test count: 5 (1 structure, 1 identity, 1 default-short validator, 1
breakthrough-length validator, 1 reject-psych-vocab)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from healteam_agents import PatientAgent, SessionContext, load_system_prompt
from healteam_memory import Actor, Event, EventType, School, Session

# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

# Default response markers — short, oral, teen voice.
PATIENT_DEFAULT_MARKERS = [
    "不知道",
    "还行",
    "嗯",
    "没",
    "……",
    "随便",
    "反",
    "他",
    "你",
    "不想",
    "不怕",
]

PATIENT_BREAKTHROUGH_MARKERS = [
    "我",
    "怕",
    "不是",
    "我害怕",
    "说不",
    "怎么",
    "我",
    "你们",
    "你们都不",
    "你们都",
]

PATIENT_FORBIDDEN_PHRASES = [
    "投射",
    "退行",
    "自我分化",
    "三角关系",
    "代际",
    "依恋",
    "防御机制",
    "作为一个 AI",
    "我意识到",
    "作为患者",
    "回避型",
    "述情障碍",
    "对立违抗",
]

PATIENT_HARD_MAX_CHARS = 80
PATIENT_DEFAULT_MAX_CHARS = 40   # default reply should be terse
PATIENT_BREAKTHROUGH_MAX_CHARS = 60  # breakthrough reply can be longer


def validate_patient_default(text: str) -> tuple[bool, list[str]]:
    """Validator for default (non-breakthrough) patient replies: very short."""
    violations: list[str] = []
    if not text or not text.strip():
        violations.append("empty output")
    if len(text) > PATIENT_DEFAULT_MAX_CHARS:
        violations.append(f"too long for default: {len(text)} > {PATIENT_DEFAULT_MAX_CHARS}")
    for phrase in PATIENT_FORBIDDEN_PHRASES:
        if phrase in text:
            violations.append(f"forbidden phrase: {phrase!r}")
    return (len(violations) == 0, violations)


def validate_patient_breakthrough(text: str) -> tuple[bool, list[str]]:
    """Validator for breakthrough patient replies: longer but still < 80."""
    violations: list[str] = []
    if not text or not text.strip():
        violations.append("empty output")
    if len(text) > PATIENT_HARD_MAX_CHARS:
        violations.append(f"too long: {len(text)} > {PATIENT_HARD_MAX_CHARS}")
    for phrase in PATIENT_FORBIDDEN_PHRASES:
        if phrase in text:
            violations.append(f"forbidden phrase: {phrase!r}")
    return (len(violations) == 0, violations)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_patient_prompt_file_structure(prompts_dir: Path) -> None:
    p = prompts_dir / "patient.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 1500
    assert "防御模式" in text, "patient.md must describe defense patterns"

    for marker in [
        "## 1. 你是谁",
        "## 2. 核心职责",
        "## 3. 行为约束",
        "## 4. 输出格式",
        "## 5. 一致性约束",
        "## 6. 与其他 Agent 的边界",
    ]:
        assert marker in text, f"patient.md missing section {marker!r}"

    # Patient-specific defenses
    for defense in ["回避", "躯体化", "退行", "反向形成", "理想化"]:
        assert defense in text, f"patient.md should mention defense {defense!r}"


def test_patient_agent_identity_and_path() -> None:
    agent = PatientAgent()
    assert agent.name == "patient"
    assert agent.role == "AI 病人"
    assert agent.system_prompt_path == "patient.md"

    prompt = load_system_prompt(agent.system_prompt_path)
    assert prompt and "16" in prompt, "patient.md should mention his age (16)"
    assert prompt and "拒学" in prompt, "patient.md should mention truancy"


def test_patient_default_validator_accepts_short_reply() -> None:
    """Default replies are 5-30 chars, no psych vocabulary, oral style."""
    good = "……不知道。"
    ok, violations = validate_patient_default(good)
    assert ok, f"good default reply rejected: {violations}"

    good2 = "没为什么。就是不想去。"
    ok2, violations2 = validate_patient_default(good2)
    assert ok2, f"good default reply 2 rejected: {violations2}"


def test_patient_breakthrough_validator_accepts_longer_reply() -> None:
    """A breakthrough reply can be 30-60 chars and contain real affect."""
    breakthrough = (
        "你们**都**是这样的。先说'我理解你'，然后**又**说'你应该这样这样'……"
    )
    ok, violations = validate_patient_breakthrough(breakthrough)
    assert ok, f"good breakthrough rejected: {violations}"
    assert any(m in breakthrough for m in PATIENT_BREAKTHROUGH_MARKERS)


def test_patient_validator_rejects_psych_vocab_and_oversize() -> None:
    """Bad: psych vocabulary, oversize, or persona break."""
    bad_psych = "我意识到我有回避型依恋和述情障碍。"
    ok, violations = validate_patient_default(bad_psych)
    assert not ok, "psych-vocab should be rejected"
    assert any("回避型" in v or "述情障碍" in v or "意识到" in v for v in violations), (
        f"expected forbidden-phrase violation, got {violations}"
    )

    long_text = "x" * 200
    ok2, violations2 = validate_patient_default(long_text)
    assert not ok2, "oversize should be rejected"
    assert any("too long" in v for v in violations2)
