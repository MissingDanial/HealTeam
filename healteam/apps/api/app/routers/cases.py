"""Cases API router — loads case JSON files from data/cases/."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..resources import CaseResourceError, cases_directory, load_validated_case

router = APIRouter()


class CaseSummary(BaseModel):
    case_id: str
    title: str
    presenting_problem: str


class CaseDetail(BaseModel):
    case_id: str
    version: str
    title: str
    presenting_problem: str
    family: dict
    defense_patterns: dict
    milestones: list
    opening_context: dict
    school_affinity: dict


@router.get("", response_model=list[CaseSummary], summary="List available cases")
async def list_cases() -> list[CaseSummary]:
    cases: list[CaseSummary] = []
    try:
        for f in sorted(cases_directory().glob("*.json")):
            data = load_validated_case(f.stem)
            cases.append(
                CaseSummary(
                    case_id=data.get("case_id", f.stem),
                    title=data.get("title", ""),
                    presenting_problem=data.get("presenting_problem", ""),
                )
            )
    except CaseResourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from None
    return cases


@router.get("/{case_id}", response_model=CaseDetail, summary="Get case details")
async def get_case(case_id: str) -> CaseDetail:
    try:
        data = load_validated_case(case_id)
    except CaseResourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from None
    return CaseDetail(
        case_id=data.get("case_id", case_id),
        version=data["version"],
        title=data.get("title", ""),
        presenting_problem=data.get("presenting_problem", ""),
        family=data.get("family", {}),
        defense_patterns={
            m: data.get("family", {}).get(m, {}).get("defense_pattern", [])
            for m in ["father", "mother", "patient"]
        },
        milestones=data.get("expected_milestones", []),
        opening_context=data.get("opening_context", {}),
        school_affinity=data.get("school_affinity", {}),
    )
