"""Case data loader for HealTeam.

Loads case JSON files and extracts structured data for agents:
- Family profiles (defense patterns, emotional registers, speaking styles)
- Opening context (scene, atmosphere, emotional states)
- Expected milestones
- Agent role hints
- Treatment focus areas
- School templates
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from healteam_memory import Actor, School


# Locate data directories relative to this file.
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parents[1]
_CASES_DIR = _REPO_ROOT / "data" / "cases"
_SCHOOLS_DIR = _REPO_ROOT / "data" / "schools"


# ---------------------------------------------------------------------------
# Core loaders
# ---------------------------------------------------------------------------


def load_case_json(case_id: str) -> dict[str, Any]:
    """Load a case JSON file by ID.

    Looks in `data/cases/{case_id}.json`.
    """
    path = _CASES_DIR / f"{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Case file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_school_template(school: School) -> str:
    """Load a therapy school template markdown by enum.

    Looks in `data/schools/{school.value}.md`.
    """
    path = _SCHOOLS_DIR / f"{school.value}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def extract_family_profiles(case: dict[str, Any]) -> dict[Actor, dict[str, Any]]:
    """Extract per-actor profiles from case JSON.

    Returns a dict mapping Actor to profile info (name, age, occupation, trait,
    emotional_register, defense_pattern, origin_family_note).
    """
    family = case.get("family", {})
    actor_map = {
        Actor.FATHER: family.get("father", {}),
        Actor.MOTHER: family.get("mother", {}),
        Actor.PATIENT: family.get("patient", {}),
    }
    profiles: dict[Actor, dict[str, Any]] = {}
    for actor, data in actor_map.items():
        if data:
            profiles[actor] = {
                "name": data.get("name", actor.value),
                "age": data.get("age"),
                "gender": data.get("gender"),
                "occupation": data.get("occupation", ""),
                "trait": data.get("trait", ""),
                "emotional_register": data.get("emotional_register", ""),
                "defense_pattern": data.get("defense_pattern", []),
                "origin_family_note": data.get("origin_family_note", ""),
            }
    return profiles


def extract_opening_context(case: dict[str, Any]) -> dict[str, Any]:
    """Extract opening scene context from case JSON."""
    return case.get("opening_context", {})


def extract_milestones(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract expected milestones from case JSON."""
    return case.get("expected_milestones", [])


def extract_agent_hints(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract agent role hints (speaking style, first-utterance drafts)."""
    return case.get("agent_role_hints", {})


def extract_defense_patterns(case: dict[str, Any]) -> dict[Actor, list[dict[str, Any]]]:
    """Extract defense pattern arrays per family member."""
    family = case.get("family", {})
    result: dict[Actor, list[dict[str, Any]]] = {}
    actor_map = {
        Actor.FATHER: "father",
        Actor.MOTHER: "mother",
        Actor.PATIENT: "patient",
    }
    for actor, key in actor_map.items():
        member = family.get(key, {})
        result[actor] = member.get("defense_pattern", [])
    return result


def extract_treatment_focus(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract treatment focus areas from case JSON."""
    return case.get("treatment_focus", [])


def extract_family_pattern(case: dict[str, Any]) -> dict[str, Any]:
    """Extract family pattern analysis (structural/Bowen/Satir)."""
    return case.get("family_pattern", {})


def extract_school_affinity(case: dict[str, Any]) -> dict[str, Any]:
    """Extract per-school suitability ratings and key techniques."""
    return case.get("school_affinity", {})


# ---------------------------------------------------------------------------
# Aggregated context builder
# ---------------------------------------------------------------------------


def build_case_context(case_id: str, school: School) -> dict[str, Any]:
    """Load a case and build a complete context dict for the orchestrator.

    Returns a dict with all extracted data, ready to be stored on TherapyState.
    """
    case = load_case_json(case_id)
    return {
        "case_id": case_id,
        "case": case,
        "family_profiles": extract_family_profiles(case),
        "opening_context": extract_opening_context(case),
        "milestones": extract_milestones(case),
        "agent_hints": extract_agent_hints(case),
        "defense_patterns": extract_defense_patterns(case),
        "treatment_focus": extract_treatment_focus(case),
        "family_pattern": extract_family_pattern(case),
        "school_affinity": extract_school_affinity(case),
        "school_template": load_school_template(school),
        "school": school.value,
    }


__all__ = [
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
