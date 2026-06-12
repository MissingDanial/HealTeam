"""Persona-consistency tests for the AI 母亲 (Mother) prompt.

The mother is a 45-year-old teacher with the **overprotection / suppressed
anger / somatization** defense pattern. Per PRD §6.4:
- Speaks 40~100 chars, hard cap 150
- Uses emotional language ("我每天", "我真的")
- Defends by overprotecting ("他其实不是...")
- Doesn't directly attack the husband; uses "反正" / sighs

Test count: 4
"""
from __future__ import annotations

from pathlib import Path

import pytest

from healteam_agents import MotherAgent, SessionContext, load_system_prompt
from healteam_memory import Actor, Event, EventType, School, Session

# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

MOTHER_DEFENSE_MARKERS = [
    "他其实",
    "他心重",
    "我管得",
    "我也是为他好",
    "反正",
    "我每天",
    "我真的",
    "睡不好",
    "管得",
    "早就",
    "算了",
]

MOTHER_FORBIDDEN_PHRASES = [
    "投射",
    "退行",
    "自我分化",
    "三角关系",
    "代际",
    "依恋",
    "防御机制",
    "作为一个 AI",
    "我意识到",
    "作为母亲",
]

MOTHER_HARD_MAX_CHARS = 150


def validate_mother_output(text: str) -> tuple[bool, list[str]]:
    violations: list[str] = []
    if not text or not text.strip():
        violations.append("empty output")
    if len(text) > MOTHER_HARD_MAX_CHARS:
        violations.append(f"too long: {len(text)} > {MOTHER_HARD_MAX_CHARS}")
    for phrase in MOTHER_FORBIDDEN_PHRASES:
        if phrase in text:
            violations.append(f"forbidden phrase: {phrase!r}")
    return (len(violations) == 0, violations)


def has_mother_defense_marker(text: str) -> bool:
    return any(m in text for m in MOTHER_DEFENSE_MARKERS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mother_prompt_file_structure(prompts_dir: Path) -> None:
    p = prompts_dir / "mother.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 1500
    assert "防御模式" in text, "mother.md must describe defense patterns"

    for marker in [
        "## 1. 你是谁",
        "## 2. 核心职责",
        "## 3. 行为约束",
        "## 4. 输出格式",
        "## 5. 一致性约束",
        "## 6. 与其他 Agent 的边界",
    ]:
        assert marker in text, f"mother.md missing section {marker!r}"

    # Mother-specific defenses
    for defense in ["过度保护", "压抑的愤怒", "自责", "躯体化"]:
        assert defense in text, f"mother.md should mention defense {defense!r}"


def test_mother_agent_identity_and_path() -> None:
    agent = MotherAgent()
    assert agent.name == "mother"
    assert agent.role == "AI 母亲"
    assert agent.system_prompt_path == "mother.md"

    prompt = load_system_prompt(agent.system_prompt_path)
    assert prompt and ("教师" in prompt or "老师" in prompt), "mother.md should describe her occupation"


def test_mother_validator_accepts_canonical_overprotection() -> None:
    """A canonical 'good' mother response (overprotection + suppressed anger)."""
    good = (
        "他**其实**不是不想去，他就是**怕**……医生您**轻点**问，"
        "我每天晚上都睡不好。他爸**根本**不管……**反正**我说什么也没用。"
    )
    ok, violations = validate_mother_output(good)
    assert ok, f"good mother response rejected: {violations}"
    assert has_mother_defense_marker(good), "expected at least one defense marker"


def test_mother_validator_rejects_psych_vocab_and_persona_break() -> None:
    """Mother must NOT use psych vocabulary; must NOT break persona."""
    bad_psych = (
        "我意识到我对儿子有过度卷入，我的依恋模式是焦虑型的。"
    )
    ok, violations = validate_mother_output(bad_psych)
    assert not ok, "psych-vocab response should be rejected"
    assert any("依恋" in v or "意识到" in v for v in violations)

    # Persona break
    bad_persona = "作为一个 AI 母亲，我的防御机制是过度保护。"
    ok2, violations2 = validate_mother_output(bad_persona)
    assert not ok2
    assert any("AI" in v or "防御机制" in v for v in violations2)
