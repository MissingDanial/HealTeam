"""Validated case loading and projections for agent contexts.

The original case JSON is preserved on disk. Legacy names are normalized once
by CaseDefinition, before any role or milestone consumer receives data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from healteam_memory import Actor, School
from healteam_memory.resources import find_project_root

from .case_schema import CaseDefinition, validate_case_id


# Optional narrow overrides for callers/tests; resolve configured resources only
# when used, so health endpoints can still explain an invalid HEALTEAM_ROOT.
_CASES_DIR: Path | None = None
_SCHOOLS_DIR: Path | None = None


def _cases_directory() -> Path:
    return _CASES_DIR if _CASES_DIR is not None else find_project_root() / "data" / "cases"


def _schools_directory() -> Path:
    return _SCHOOLS_DIR if _SCHOOLS_DIR is not None else find_project_root() / "data" / "schools"


def _school(value: School | str) -> School:
    try:
        return School(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Unknown therapy school: {value!r}") from exc


def load_case_definition(case_id: str) -> CaseDefinition:
    """Load and validate a safe identifier and its complete case payload."""
    validate_case_id(case_id)
    path = _cases_directory() / f"{case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Case file not found: {path}")
    case = CaseDefinition.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if case.case_id != case_id:
        raise ValueError(f"case_id {case.case_id!r} does not match requested case_id {case_id!r}")
    return case


def load_case_json(case_id: str) -> dict[str, Any]:
    """Return the validated canonical JSON contract, not unchecked file data."""
    return load_case_definition(case_id).model_dump(mode="json", exclude_none=True)


def load_school_template(school: School | str) -> str:
    """Load the selected school explicitly; absent/empty resources are errors."""
    selected = _school(school)
    path = _schools_directory() / f"{selected.value}.md"
    if not path.is_file():
        raise FileNotFoundError(f"School template not found: {path}")
    template = path.read_text(encoding="utf-8")
    if not template.strip():
        raise ValueError(f"School template is empty: {path}")
    return template


def _definition(case: CaseDefinition | dict[str, Any]) -> CaseDefinition:
    return case if isinstance(case, CaseDefinition) else CaseDefinition.model_validate(case)


def extract_family_profiles(case: CaseDefinition | dict[str, Any]) -> dict[Actor, dict[str, Any]]:
    family = _definition(case).family.model_dump(mode="json", exclude_none=True)
    return {Actor(role): profile for role, profile in family.items()}


def extract_opening_context(case: CaseDefinition | dict[str, Any]) -> dict[str, Any]:
    return _definition(case).opening_context.model_dump(mode="json")


def extract_milestones(case: CaseDefinition | dict[str, Any]) -> list[dict[str, Any]]:
    return [milestone.model_dump(mode="json") for milestone in _definition(case).expected_milestones]


def extract_agent_hints(case: CaseDefinition | dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _definition(case).agent_role_hints.model_dump(mode="json")


def extract_defense_patterns(case: CaseDefinition | dict[str, Any]) -> dict[Actor, list[dict[str, Any]]]:
    return {actor: profile["defense_pattern"] for actor, profile in extract_family_profiles(case).items()}


def extract_treatment_focus(case: CaseDefinition | dict[str, Any]) -> list[dict[str, Any]]:
    return [focus.model_dump(mode="json") for focus in _definition(case).treatment_focus]


def extract_family_pattern(case: CaseDefinition | dict[str, Any]) -> dict[str, Any]:
    return _definition(case).family_pattern.model_dump(mode="json")


def extract_school_affinity(case: CaseDefinition | dict[str, Any]) -> dict[str, Any]:
    return _definition(case).school_affinity.model_dump(mode="json")


def build_case_context(case_id: str, school: School | str) -> dict[str, Any]:
    """Validate at the boundary and project one canonical case for all roles."""
    selected = _school(school)
    case = load_case_definition(case_id)
    return {
        "case_id": case.case_id,
        "case_version": case.version,
        "case": case.model_dump(mode="json", exclude_none=True),
        "family_profiles": extract_family_profiles(case),
        "opening_context": extract_opening_context(case),
        "milestones": extract_milestones(case),
        "agent_hints": extract_agent_hints(case),
        "defense_patterns": extract_defense_patterns(case),
        "treatment_focus": extract_treatment_focus(case),
        "family_pattern": extract_family_pattern(case),
        "school_affinity": extract_school_affinity(case),
        "school_template": load_school_template(selected),
        "school": selected.value,
    }


__all__ = [
    "load_case_definition",
    "load_case_json",
    "load_school_template",
    "extract_family_profiles",
    "extract_opening_context",
    "extract_milestones",
    "extract_agent_hints",
    "extract_defense_patterns",
    "extract_treatment_focus",
    "extract_family_pattern",
    "extract_school_affinity",
    "build_case_context",
]
