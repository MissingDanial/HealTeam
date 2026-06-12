"""Cases API router — loads case JSON files from data/cases/."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "cases"


class CaseSummary(BaseModel):
    case_id: str
    title: str
    presenting_problem: str


class CaseDetail(BaseModel):
    case_id: str
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
    if not DATA_DIR.exists():
        return cases
    for f in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            cases.append(CaseSummary(
                case_id=data.get("case_id", f.stem),
                title=data.get("title", ""),
                presenting_problem=data.get("presenting_problem", ""),
            ))
        except Exception:
            continue
    return cases


@router.get("/{case_id}", response_model=CaseDetail, summary="Get case details")
async def get_case(case_id: str) -> CaseDetail:
    case_path = DATA_DIR / f"{case_id}.json"
    if not case_path.exists():
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    data = json.loads(case_path.read_text(encoding="utf-8"))
    return CaseDetail(
        case_id=data.get("case_id", case_id),
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
