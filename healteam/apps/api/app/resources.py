"""Validated resources for API preflight and readiness checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

# Installed packages need no bootstrap. Source checkouts locate code, not data,
# by the bootstrap marker, independently from the configurable resource root.
try:
    from healteam_memory.resources import ResourceConfigurationError, find_project_root
except ModuleNotFoundError:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "repo_bootstrap.py").is_file():
            sys.path.insert(0, str(candidate))
            from repo_bootstrap import bootstrap_local_packages

            bootstrap_local_packages(candidate, strict=False)
            break
    from healteam_memory.resources import ResourceConfigurationError, find_project_root

from healteam_agents.case_schema import CaseDefinition, validate_case_id

CASE_ID_PATTERN = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
SCHOOLS = ("structural", "bowen", "satir")
PROMPTS = ("doctor", "father", "mother", "patient", "observer", "psychiatrist", "orchestrator")


class CaseResourceError(ValueError):
    def __init__(self, code: str, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def project_root() -> Path:
    try:
        return find_project_root(start=Path(__file__))
    except (ResourceConfigurationError, OSError, ValueError):
        raise CaseResourceError("resources_unavailable", 503) from None


def cases_directory() -> Path:
    return project_root() / "data" / "cases"


def load_validated_case(case_id: str) -> dict[str, Any]:
    try:
        validate_case_id(case_id)
    except ValueError:
        raise CaseResourceError("invalid_case_id") from None
    directory = cases_directory().resolve()
    path = (directory / f"{case_id}.json").resolve()
    if not path.is_relative_to(directory):
        raise CaseResourceError("invalid_case_path")
    if not path.is_file():
        raise CaseResourceError("case_not_found", 404)
    try:
        case = CaseDefinition.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
        raise CaseResourceError("invalid_case_data") from None
    if case.case_id != case_id:
        raise CaseResourceError("case_id_mismatch")
    return case.model_dump(mode="json", exclude_none=True)


def validate_supporting_resources(school: str | None = None) -> None:
    root = project_root()
    schools = (school,) if school is not None else SCHOOLS
    for value in schools:
        if value not in SCHOOLS:
            raise CaseResourceError("invalid_school")
        try:
            if not (root / "data" / "schools" / f"{value}.md").read_text(encoding="utf-8").strip():
                raise CaseResourceError("school_resource_unavailable", 503)
        except (OSError, UnicodeError):
            raise CaseResourceError("school_resource_unavailable", 503) from None
    for prompt in PROMPTS:
        try:
            if not (root / "packages" / "prompts" / f"{prompt}.md").read_text(encoding="utf-8").strip():
                raise CaseResourceError("prompt_resource_unavailable", 503)
        except (OSError, UnicodeError):
            raise CaseResourceError("prompt_resource_unavailable", 503) from None


def check_readiness_resources() -> None:
    from .services.rubrics import snapshot_rubric

    files = sorted(cases_directory().glob("*.json"))
    if not files:
        raise CaseResourceError("cases_unavailable", 503)
    for path in files:
        case = load_validated_case(path.stem)
        for school in SCHOOLS:
            snapshot_rubric(school, case)
    validate_supporting_resources()
