"""Persona-consistency tests for the AI 心理医生 (Psychiatrist) prompt.

The psychiatrist is the **silent** agent during live session — it only speaks
in the post-session `supervision_meeting`. These tests verify:
1. The prompt file contains the 'silent during live session' constraint.
2. The agent's `should_speak` returns False for any live-session context
   (its contract is to stay silent).
3. The prompt contains theoretical-citation guidance for the supervision phase.
4. A canonical 'good' supervision-meeting response passes a role validator.

Test count: 4
"""
from __future__ import annotations

from pathlib import Path

import pytest

from healteam_agents import PsychiatristAgent, SessionContext, load_system_prompt
from healteam_memory import Actor, School, Session

# ---------------------------------------------------------------------------
# Role-specific output validator (for the supervision phase)
# ---------------------------------------------------------------------------

# A canonical 'good' supervision-meeting response should:
# - 80~250 chars
# - include a theorist citation (Minuchin / Bowen / Satir)
# - NOT use first-person affective language (e.g. "我感到", "我觉得")
# - include a year + book/journal reference (e.g. 1974, 1981)
PSYCH_FORBIDDEN_PHRASES = [
    "我感到",       # no first-person affect
    "我觉得",       # no first-person opinion
    "你应该",       # no instructions to the trainee
    "下次治疗你可以",  # no specific operational advice
]


def validate_psychiatrist_output(text: str) -> tuple[bool, list[str]]:
    """Validator for supervision-meeting responses."""
    violations: list[str] = []
    if not text or not text.strip():
        violations.append("empty output")
    if len(text) > 350:  # hard cap, slightly above 250 nominal
        violations.append(f"too long: {len(text)} > 350")
    for phrase in PSYCH_FORBIDDEN_PHRASES:
        if phrase in text:
            violations.append(f"forbidden phrase: {phrase!r}")
    return (len(violations) == 0, violations)


def _has_theorist_citation(text: str) -> bool:
    """At least one theorist surname + year should appear in supervision output."""
    names = ["Minuchin", "Bowen", "Satir", "Kerr", "Tomm", "White"]
    has_name = any(n in text for n in names)
    has_year = any(y in text for y in ("1974", "1978", "1981", "1988", "1991"))
    return has_name and has_year


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_psychiatrist_prompt_file_structure(prompts_dir: Path) -> None:
    """psychiatrist.md has the required structure and the
    'silent during live session' constraint.
    """
    p = prompts_dir / "psychiatrist.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 1500

    # The single most important constraint (verifier grep check)
    assert "不直接发言" in text and "督导会" in text, (
        "psychiatrist.md must say it does NOT speak directly, only at supervision meeting"
    )
    # And the structural sections
    for marker in [
        "## 1. 你是谁",
        "## 2. 核心职责",
        "## 3. 行为约束",
        "## 4. 输出格式",
        "## 5. 一致性约束",
        "## 6. 与其他 Agent 的边界",
    ]:
        assert marker in text, f"psychiatrist.md missing section {marker!r}"
    # Should NOT prescribe operational advice (the literal "下次治疗你可以"
    # phrase appears inside the rule that says "this kind of phrase should not
    # be said", so we test the spirit: the prompt must include the
    # operational-advice prohibition rule itself).
    assert "不给具体治疗建议" in text or "不**给**具体治疗建议" in text, (
        "psychiatrist.md should explicitly forbid operational advice"
    )


def test_psychiatrist_agent_never_speaks_in_live_session() -> None:
    """Per PRD §6.2 + the agent's W1 stub, the psychiatrist's should_speak
    is always False during live session. This is the agent's whole job.
    """
    agent = PsychiatristAgent()
    assert agent.name == "psychiatrist"
    assert agent.role == "AI 心理医生"
    assert agent.system_prompt_path == "psychiatrist.md"

    # Try a variety of contexts; should_speak must always be False.
    for round_num in [0, 5, 15, 25]:
        session = Session(case_id="teen_truancy_01", school=School.STRUCTURAL)
        session.round = round_num
        ctx = SessionContext(
            session=session,
            speaker_candidates=[Actor.DOCTOR, Actor.PSYCHIATRIST, Actor.FATHER, Actor.MOTHER, Actor.PATIENT],
        )
        assert agent.should_speak(ctx) is False, (
            f"psychiatrist should never speak in live session (round={round_num})"
        )


def test_psychiatrist_prompt_enumerates_theoretical_references() -> None:
    """The prompt must list the three primary theorists (Minuchin / Bowen /
    Satir) so the supervision phase has explicit guidance on who to cite.
    """
    text = load_system_prompt("psychiatrist.md")
    assert "Minuchin" in text
    assert "Bowen" in text
    assert "Satir" in text


def test_psychiatrist_validator_accepts_canonical_supervision() -> None:
    """A canonical 'good' supervision response passes the validator."""
    good = (
        "Minuchin (1974, *Families and Family Therapy*) 强调，症状是家庭结构失衡的副产品。"
        "学员在第 8 轮过早地用了 reframing，但这一步还没有建立 enact 的基础——"
        "结构派要求先让家庭成员在当下演示互动模式，再做意义重构。"
    )
    ok, violations = validate_psychiatrist_output(good)
    assert ok, f"good supervision response rejected: {violations}"
    assert _has_theorist_citation(good)


def test_psychiatrist_validator_rejects_first_person_affect_and_advice() -> None:
    """Bad supervision responses are rejected."""
    bad_affect = "我感到学员做得不太好。我觉得他们缺乏共情。"
    ok, violations = validate_psychiatrist_output(bad_affect)
    assert not ok, "first-person affect should be rejected"
    assert any("我感到" in v or "我觉得" in v for v in violations)

    bad_advice = "下次治疗你可以尝试在第 3 轮就引入三角化提问。"
    ok2, violations2 = validate_psychiatrist_output(bad_advice)
    assert not ok2, "operational advice should be rejected"
    assert any("下次治疗你可以" in v for v in violations2)
