"""Case boundaries must feed actual agent inputs, not just contain JSON keys."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from healteam_memory import Actor, School, Session
from healteam_agents import case_loader
from healteam_agents.base import SessionContext
from healteam_agents.doctor import DoctorAgent
from healteam_agents.father import FatherAgent
from healteam_agents.mother import MotherAgent
from healteam_agents.patient import PatientAgent


CASE_ID = "teen-truancy-01"
CASE_PATH = Path(__file__).resolve().parents[2] / "data" / "cases" / f"{CASE_ID}.json"


@pytest.fixture
def legacy_case() -> dict:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def _agent_context(case_context: dict) -> SessionContext:
    return SessionContext(
        session=Session(case_id=CASE_ID, school=School.STRUCTURAL),
        family_profiles=case_context["family_profiles"],
        opening_context=case_context["opening_context"],
        defense_patterns=case_context["defense_patterns"],
        agent_role_hints=case_context["agent_hints"],
        milestones=case_context["milestones"],
        treatment_focus=case_context["treatment_focus"],
        family_pattern=case_context["family_pattern"],
    )


@pytest.mark.parametrize(
    "actor,agent", [(Actor.FATHER, FatherAgent()), (Actor.MOTHER, MotherAgent()), (Actor.PATIENT, PatientAgent())]
)
def test_legacy_case_content_reaches_family_agent_inputs(legacy_case: dict, actor: Actor, agent) -> None:
    context = _agent_context(case_loader.build_case_context(CASE_ID, School.STRUCTURAL))
    user_message = agent._build_user_message(context)
    system_prompt = agent._build_system_prompt(context)
    opening = legacy_case["opening_context"]
    role_hints = legacy_case["agent_role_hints"][f"{actor.value}_agent"]

    assert opening["session_round_0_scene"] in user_message
    assert opening["emotional_state"][actor.value] in user_message
    assert role_hints["speaking_style"] in system_prompt
    for draft in role_hints["first_3_utterances_draft"]:
        assert draft in system_prompt
    assert legacy_case["family"][actor.value]["defense_pattern"][0]["example_utterance"] in system_prompt


def test_legacy_case_content_reaches_doctor_inputs(legacy_case: dict) -> None:
    context = _agent_context(case_loader.build_case_context(CASE_ID, School.STRUCTURAL))
    doctor = DoctorAgent()
    user_message = doctor._build_user_message(context)
    system_prompt = doctor._build_system_prompt(context)

    for cue in legacy_case["opening_context"]["atmospheric_cues"]:
        assert cue in user_message
    for milestone in legacy_case["expected_milestones"]:
        assert milestone["label"] in user_message
    for school in School:
        for value in legacy_case["family_pattern"][f"{school.value}_analysis"].values():
            assert str(value) in system_prompt
    for focus in legacy_case["treatment_focus"]:
        assert focus["title"] in system_prompt


def test_milestones_expose_both_display_and_detector_fields(legacy_case: dict) -> None:
    context = case_loader.build_case_context(CASE_ID, School.STRUCTURAL)
    for source, milestone in zip(legacy_case["expected_milestones"], context["milestones"], strict=True):
        assert milestone["label"] == source["label"]
        assert milestone["description"] == source["label"]
        assert milestone["validation_signals"] == [source["validation_signal"]]


def _normalize(case: dict) -> dict:
    from healteam_agents.case_schema import CaseDefinition

    return CaseDefinition.model_validate(case).model_dump(mode="json", exclude_none=True)


def test_normalization_preserves_source_and_is_idempotent(legacy_case: dict) -> None:
    original = deepcopy(legacy_case)
    canonical = _normalize(legacy_case)
    assert legacy_case == original
    assert _normalize(canonical) == canonical
    assert canonical["case_id"] == original["case_id"]
    assert canonical["version"] == original["version"]
    assert canonical["clinical_notes"] == original["clinical_notes"]


@pytest.mark.parametrize(
    "path,value",
    [
        (("misspelled_summary",), "unexpected"),
        (("family", "father", "unkown_trait"), "unexpected"),
        (("opening_context", "unknown_scene"), "unexpected"),
        (("expected_milestones", 0, "validation_signlas"), ["unexpected"]),
        (("family_pattern", "other_school"), {}),
        (("agent_role_hints", "doctor_agent"), {}),
        (("school_affinity", "other_school"), {}),
    ],
)
def test_unknown_fields_fail_instead_of_being_silently_discarded(legacy_case: dict, path: tuple, value) -> None:
    target = legacy_case
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        _normalize(legacy_case)


@pytest.mark.parametrize("field", ["case_id", "version", "family", "opening_context", "expected_milestones"])
def test_missing_required_sections_fail(legacy_case: dict, field: str) -> None:
    del legacy_case[field]
    with pytest.raises(ValidationError):
        _normalize(legacy_case)


@pytest.mark.parametrize("case_id", ["../README", "..\\README", "/absolute", "C:\\absolute", "case.json", "", " CON "])
def test_case_id_cannot_escape_case_directory(case_id: str) -> None:
    with pytest.raises(ValueError, match="case_id"):
        case_loader.load_case_json(case_id)


@pytest.mark.parametrize("version", ["", "latest", "1", "1.0", "01.0.0"])
def test_case_version_must_be_explicit_semver(legacy_case: dict, version: str) -> None:
    legacy_case["version"] = version
    with pytest.raises(ValidationError):
        _normalize(legacy_case)


def test_duplicate_milestone_ids_are_rejected(legacy_case: dict) -> None:
    legacy_case["expected_milestones"][1]["id"] = legacy_case["expected_milestones"][0]["id"]
    with pytest.raises(ValidationError, match="milestone"):
        _normalize(legacy_case)


def test_missing_family_role_is_rejected(legacy_case: dict) -> None:
    del legacy_case["family"]["patient"]
    with pytest.raises(ValidationError):
        _normalize(legacy_case)


def test_ambiguous_legacy_and_canonical_fields_are_rejected(legacy_case: dict) -> None:
    legacy_case["opening_context"]["scene"] = "conflicting scene"
    with pytest.raises(ValidationError, match="both"):
        _normalize(legacy_case)


def test_mismatched_file_and_payload_case_id_are_rejected(tmp_path: Path, monkeypatch, legacy_case: dict) -> None:
    legacy_case["case_id"] = "another-case"
    (tmp_path / f"{CASE_ID}.json").write_text(json.dumps(legacy_case, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(case_loader, "_CASES_DIR", tmp_path)
    with pytest.raises(ValueError, match="case_id"):
        case_loader.load_case_json(CASE_ID)


@pytest.mark.parametrize("school", ["other", "", "../structural", None])
def test_unknown_school_is_rejected(school) -> None:
    with pytest.raises(ValueError, match="school"):
        case_loader.build_case_context(CASE_ID, school)


def test_missing_school_template_is_an_explicit_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(case_loader, "_SCHOOLS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="template"):
        case_loader.load_school_template(School.SATIR)


def test_empty_school_template_is_an_explicit_error(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "satir.md").write_text("  \n", encoding="utf-8")
    monkeypatch.setattr(case_loader, "_SCHOOLS_DIR", tmp_path)
    with pytest.raises(ValueError, match="template"):
        case_loader.load_school_template(School.SATIR)


def test_explicit_resource_root_is_resolved_when_resources_are_loaded(tmp_path: Path, monkeypatch, legacy_case: dict) -> None:
    cases = tmp_path / "data" / "cases"
    schools = tmp_path / "data" / "schools"
    cases.mkdir(parents=True)
    schools.mkdir()
    (tmp_path / "packages" / "prompts").mkdir(parents=True)
    legacy_case["title"] = "Configured resource copy"
    (cases / f"{CASE_ID}.json").write_text(json.dumps(legacy_case, ensure_ascii=False), encoding="utf-8")
    (schools / "satir.md").write_text("CONFIGURED_SATIR_TEMPLATE", encoding="utf-8")
    # The case_loader module was already imported before this override.
    monkeypatch.setenv("HEALTEAM_ROOT", str(tmp_path))
    context = case_loader.build_case_context(CASE_ID, School.SATIR)
    assert context["case"]["title"] == "Configured resource copy"
    assert context["school_template"] == "CONFIGURED_SATIR_TEMPLATE"


def test_invalid_explicit_root_does_not_silently_use_source_resources(tmp_path: Path, monkeypatch) -> None:
    from healteam_memory.resources import ResourceConfigurationError

    monkeypatch.setenv("HEALTEAM_ROOT", str(tmp_path))
    with pytest.raises(ResourceConfigurationError, match="HEALTEAM_ROOT"):
        case_loader.load_case_json(CASE_ID)
    with pytest.raises(ResourceConfigurationError, match="HEALTEAM_ROOT"):
        case_loader.load_school_template(School.SATIR)


def test_fresh_api_import_with_invalid_root_keeps_readiness_available(tmp_path: Path) -> None:
    repo_root = CASE_PATH.parents[2]
    script = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from app.main import create_app
from fastapi.testclient import TestClient
with TestClient(create_app()) as client:
    response = client.get('/readyz')
    assert response.status_code == 503, response.text
    assert 'resources_unavailable' in response.text
"""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("HEALTEAM_", "ANTHROPIC_", "OPENAI_", "LLM_"))}
    env.update(HEALTEAM_ROOT=str(tmp_path), HEALTEAM_MODEL_MODE="mock", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(repo_root / "apps" / "api"), str(repo_root)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
