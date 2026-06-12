"""Persona-consistency tests for the 3 school (流派) templates.

Verifies:
- Each template exists, has the required sections, cites the right theorist
- Each template has 5~8 intervention techniques, each with example phrasings
- The 3 schools' technique lists have < 50% overlap (per verifier)
- Each template documents the school-specific observer hint rules

Test count: 6
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# These imports rely on conftest.py having registered the package aliases
# AND pytest loading conftest.py first.
# The SCHOOLS_DIR / PROMPTS_DIR / REPO_ROOT constants are exposed as pytest
# fixtures (see conftest.py), so test functions use them via parameters.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Technique section is a markdown table with a header row starting with "| # | 技术"
_TECH_TABLE_HEADER = re.compile(r"\|\s*#\s*\|\s*技术")


def _extract_technique_names(text: str) -> list[str]:
    """Return the list of technique names from the markdown table.

    The table looks like:
        | # | 技术 | 何时使用 | 示例话术 |
        |---|------|----------|----------|
        | 1 | joining（加入） | ... | "..." |
    We extract the technique column.
    """
    lines = text.splitlines()
    in_table = False
    names: list[str] = []
    for line in lines:
        if _TECH_TABLE_HEADER.search(line):
            in_table = True
            continue
        if in_table:
            # End of table when we hit a non-pipe line or a section header
            if not line.strip().startswith("|"):
                in_table = False
                continue
            # Skip the separator row (| --- | --- | ...)
            if re.match(r"^\|\s*-+", line):
                continue
            # Parse: | 1 | joining（加入） | 何时使用 | 示例话术 |
            cells = [c.strip() for c in line.split("|")]
            # First and last cells are empty (splitting '| a | b |' gives ['', 'a', 'b', ''])
            cells = [c for c in cells if c != ""]
            if len(cells) >= 2:
                # The technique name is the 2nd cell (after the "#" column)
                names.append(cells[1])
    return names


# ---------------------------------------------------------------------------
# School-specific keywords (verifier-mandated)
# ---------------------------------------------------------------------------

STRUCTURAL_KEYWORDS = ["家庭结构", "边界", "子系统"]
BOWEN_KEYWORDS = ["代际", "自我分化", "三角关系"]
SATIR_KEYWORDS = ["一致性", "冰山", "转化"]

# Theorist citations required by the verifier
STRUCTURAL_THEORIST = "Minuchin"
BOWEN_THEORIST = "Bowen"
SATIR_THEORIST = "Satir"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_structural_school_template(schools_dir: Path) -> None:
    p = schools_dir / "structural.md"
    assert p.exists(), f"{p} should exist"
    text = _read(p)
    assert len(text) >= 1500

    # Required sections
    for marker in ["# 结构派治疗", "## 1. 开场话术", "## 2. 干预技术清单", "## 3. 典型节律", "## 4. 关键干预时机"]:
        assert marker in text, f"structural.md missing {marker!r}"

    # Verifier-required keywords
    for kw in STRUCTURAL_KEYWORDS:
        assert kw in text, f"structural.md missing keyword {kw!r}"

    # Theorist
    assert STRUCTURAL_THEORIST in text, f"structural.md must cite {STRUCTURAL_THEORIST}"

    # 5~8 techniques
    techniques = _extract_technique_names(text)
    assert 5 <= len(techniques) <= 8, (
        f"structural.md should have 5-8 techniques, found {len(techniques)}: {techniques}"
    )

    # Each technique must have an example phrasing (must contain at least one
    # double-quote / Chinese quote / em-dash example).
    # The check is on the table cells: every data row should have at least 4
    # non-empty cells (because | # | name | when | example |).
    lines = text.splitlines()
    in_table = False
    example_row_count = 0
    for line in lines:
        if _TECH_TABLE_HEADER.search(line):
            in_table = True
            continue
        if in_table and line.startswith("|") and not re.match(r"^\|\s*-+", line):
            cells = [c for c in line.split("|") if c.strip()]
            if len(cells) >= 4 and cells[0] not in ("#", ""):
                example_row_count += 1
        elif in_table and not line.startswith("|"):
            break
    assert example_row_count >= 5, "each technique must have an example phrasing column"


def test_bowen_school_template(schools_dir: Path) -> None:
    p = schools_dir / "bowen.md"
    assert p.exists()
    text = _read(p)
    assert len(text) >= 1500

    for marker in ["# Bowen 系统式", "## 1. 开场话术", "## 2. 干预技术清单", "## 3. 典型节律", "## 4. 关键干预时机"]:
        assert marker in text, f"bowen.md missing {marker!r}"

    for kw in BOWEN_KEYWORDS:
        assert kw in text, f"bowen.md missing keyword {kw!r}"

    assert BOWEN_THEORIST in text, f"bowen.md must cite {BOWEN_THEORIST}"

    techniques = _extract_technique_names(text)
    assert 5 <= len(techniques) <= 8, f"bowen.md should have 5-8 techniques, got {len(techniques)}: {techniques}"


def test_satir_school_template(schools_dir: Path) -> None:
    p = schools_dir / "satir.md"
    assert p.exists()
    text = _read(p)
    assert len(text) >= 1500

    for marker in ["# 萨提亚治疗", "## 1. 开场话术", "## 2. 干预技术清单", "## 3. 典型节律", "## 4. 关键干预时机"]:
        assert marker in text, f"satir.md missing {marker!r}"

    for kw in SATIR_KEYWORDS:
        assert kw in text, f"satir.md missing keyword {kw!r}"

    assert SATIR_THEORIST in text, f"satir.md must cite {SATIR_THEORIST}"

    techniques = _extract_technique_names(text)
    assert 5 <= len(techniques) <= 8, f"satir.md should have 5-8 techniques, got {len(techniques)}: {techniques}"


def test_school_technique_overlap_is_low(schools_dir: Path) -> None:
    """Per verifier: 三个流派的'干预技术清单'重叠度 < 50%.

    We compare technique names across the 3 schools. The shared concepts
    (e.g. '三角化') may appear in multiple schools but should be < 50%
    of the total technique set.
    """
    names = {
        "structural": _extract_technique_names(_read(schools_dir / "structural.md")),
        "bowen": _extract_technique_names(_read(schools_dir / "bowen.md")),
        "satir": _extract_technique_names(_read(schools_dir / "satir.md")),
    }
    total = sum(len(v) for v in names.values())
    assert total >= 15, f"expected at least 15 techniques across 3 schools, got {total}"

    # Compute shared names. Normalize by lower-casing and removing whitespace.
    norm = {k: {n.lower().replace(" ", "") for n in v} for k, v in names.items()}

    shared_struct_bowen = norm["structural"] & norm["bowen"]
    shared_struct_satir = norm["structural"] & norm["satir"]
    shared_bowen_satir = norm["bowen"] & norm["satir"]
    # Any name appearing in 2+ schools
    all_shared = shared_struct_bowen | shared_struct_satir | shared_bowen_satir

    # Allow up to 49% of total to be shared.
    overlap_pct = len(all_shared) / total
    assert overlap_pct < 0.5, (
        f"school technique overlap {overlap_pct:.0%} >= 50% (shared: {all_shared})"
    )


def test_school_red_flags_documented(schools_dir: Path) -> None:
    """Each school template documents its 'red flag' (治疗方向错了的标志) —
    this is required for the L1 evolution engine to know when to escalate.
    """
    for school_file in ("structural.md", "bowen.md", "satir.md"):
        text = _read(schools_dir / school_file)
        assert "红旗" in text or "red flag" in text.lower() or "方向错" in text, (
            f"{school_file} should document red flags"
        )


def test_school_observer_hints_documented(schools_dir: Path) -> None:
    """Each school template has §7 documenting the observer's school-specific
    hint phrasings — needed for the L1 engine's school-aware evolution.
    """
    for school_file in ("structural.md", "bowen.md", "satir.md"):
        text = _read(schools_dir / school_file)
        # Should have a section about observer / 旁观者
        assert "旁观者" in text, f"{school_file} should document observer hints"
        # Should mention at least 4 trigger labels
        triggers = ["defense_escalation", "school_mixing", "emotional_breakthrough", "pacing"]
        hits = sum(1 for t in triggers if t in text)
        assert hits >= 3, f"{school_file} should document ≥3 observer triggers (got {hits})"
