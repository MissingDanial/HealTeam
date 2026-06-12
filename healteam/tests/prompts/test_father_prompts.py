"""Persona-consistency tests for the AI 父亲 (Father) prompt.

The father is a 48-year-old engineer with the **rationalization / denial /
intellectualization** defense pattern. Per PRD §6.4:
- Speaks 30~80 chars, hard cap 120
- Avoids affect ("我没什么感觉")
- Rationalizes son's truancy as school / society / wife problem
- Does NOT use professional psychological vocabulary

Test count: 4
"""
from __future__ import annotations

from pathlib import Path

import pytest

from healteam_agents import FatherAgent, SessionContext, load_system_prompt
from healteam_memory import Actor, Event, EventType, School, Session

# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

# Defense markers that, when present, indicate a 'good' father response.
FATHER_DEFENSE_MARKERS = [
    "我们那时候",
    "别的孩子",
    "现在的孩子",
    "我们",
    "我觉得",
    "其实",
    "学校",
    "老师",
    "管得",
    "没什么",
]

# Forbidden: professional psychology vocabulary
FATHER_FORBIDDEN_PHRASES = [
    "投射",
    "退行",
    "自我分化",
    "三角关系",
    "代际",
    "依恋",
    "防御机制",
    "作为一个 AI",
    "我意识到",
    "作为父亲",  # meta self-reference
]

FATHER_HARD_MAX_CHARS = 120


def validate_father_output(text: str) -> tuple[bool, list[str]]:
    violations: list[str] = []
    if not text or not text.strip():
        violations.append("empty output")
    if len(text) > FATHER_HARD_MAX_CHARS:
        violations.append(f"too long: {len(text)} > {FATHER_HARD_MAX_CHARS}")
    for phrase in FATHER_FORBIDDEN_PHRASES:
        if phrase in text:
            violations.append(f"forbidden phrase: {phrase!r}")
    return (len(violations) == 0, violations)


def has_father_defense_marker(text: str) -> bool:
    return any(m in text for m in FATHER_DEFENSE_MARKERS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_father_prompt_file_structure(prompts_dir: Path) -> None:
    p = prompts_dir / "father.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 1500

    # Verifier-required: family member prompt must mention "防御模式"
    assert "防御模式" in text, "father.md must describe defense patterns"

    # Sections
    for marker in [
        "## 1. 你是谁",
        "## 2. 核心职责",
        "## 3. 行为约束",
        "## 4. 输出格式",
        "## 5. 一致性约束",
        "## 6. 与其他 Agent 的边界",
    ]:
        assert marker in text, f"father.md missing section {marker!r}"

    # Defense patterns must be enumerated
    for defense in ["合理化", "否认", "理智化", "转移", "反向形成"]:
        assert defense in text, f"father.md should mention defense pattern {defense!r}"


def test_father_agent_identity_and_path() -> None:
    agent = FatherAgent()
    assert agent.name == "father"
    assert agent.role == "AI 父亲"
    assert agent.system_prompt_path == "father.md"

    prompt = load_system_prompt(agent.system_prompt_path)
    assert prompt and "工程师" in prompt, "father.md should describe him as engineer"


def test_father_validator_accepts_canonical_rationalization() -> None:
    """A canonical father response (rationalization) passes the validator."""
    good = (
        "我觉得吧，主要还是**学校**的问题。他原来那个班氛围挺好的，"
        "换了班之后就跟不上了……我们那时候哪有这种事。"
    )
    ok, violations = validate_father_output(good)
    assert ok, f"good father response rejected: {violations}"
    assert has_father_defense_marker(good), "expected at least one defense marker"


def test_father_validator_rejects_psych_vocab_and_oversize() -> None:
    """Bad: too long; uses psychological vocabulary; breaks persona."""
    bad_psych = (
        "我意识到我刚才对儿子有投射行为，我的反向形成导致他产生退行反应。"
    )
    ok, violations = validate_father_output(bad_psych)
    assert not ok, "psych-vocab response should be rejected"
    assert any("投射" in v or "退行" in v or "反向形成" in v for v in violations), (
        f"expected psych-vocab violation, got {violations}"
    )

    # Oversize
    long_text = "x" * 200
    ok2, violations2 = validate_father_output(long_text)
    assert not ok2, "oversize response should be rejected"
    assert any("too long" in v for v in violations2)
