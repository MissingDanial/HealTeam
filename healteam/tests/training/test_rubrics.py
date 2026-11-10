from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.resources import CaseResourceError, load_validated_case
from app.services import rubrics


@pytest.mark.parametrize(
    "school,milestones",
    [("structural", {"M1", "M2", "M3", "M4"}), ("bowen", {"M4", "M5"}), ("satir", {"M1", "M2", "M3", "M5"})],
)
def test_trial_rubric_freezes_only_selected_school_case_goals(school, milestones):
    frozen = rubrics.snapshot_rubric(school, load_validated_case("teen-truancy-01"))
    assert frozen["school"] == school
    assert frozen["status"] == "trial_unvalidated"
    assert frozen["rubric_version"] == "1.0.0"
    assert len(frozen["sha256"]) == 64
    assert {item["dimension_id"] for item in frozen["dimensions"]} == set(rubrics.DIMENSION_IDS)
    assert {item["milestone_id"] for item in frozen["milestones"]} == milestones
    assert all(item["required_actors"] for item in frozen["milestones"])
    assert "未经专家校准" in frozen["notice"]


@pytest.mark.parametrize("invalid", ["missing", "anchors", "duplicate_dimension"])
def test_missing_or_ambiguous_trial_rubric_refuses_new_snapshot(tmp_path, monkeypatch, invalid):
    source = Path(rubrics.project_root()) / "data" / "rubrics" / rubrics.RUBRIC_FILE
    data = json.loads(source.read_text(encoding="utf-8"))
    if invalid == "anchors":
        data["dimensions"][0]["anchors"].pop("0")
    if invalid == "duplicate_dimension":
        data["dimensions"][1]["dimension_id"] = data["dimensions"][0]["dimension_id"]
    directory = tmp_path / "data" / "rubrics"
    directory.mkdir(parents=True)
    if invalid != "missing":
        (directory / rubrics.RUBRIC_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    case = load_validated_case("teen-truancy-01")
    monkeypatch.setattr(rubrics, "project_root", lambda: tmp_path)
    with pytest.raises(CaseResourceError) as failure:
        rubrics.snapshot_rubric("satir", case)
    assert failure.value.code == "rubric_resource_unavailable"
