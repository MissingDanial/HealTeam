"""Export executable session contracts; --check verifies checked-in examples."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.response_models import MessageReceipt, SessionHistory, SessionView, WorkflowReport
from healteam_agents.case_schema import CaseDefinition
from healteam_memory.contracts import (
    CONTRACT_VERSION,
    CreateSessionCommand,
    EventEnvelope,
    SessionSnapshot,
    SubmitMessageCommand,
    UsageSummary,
)
from healteam_memory.evaluation_contracts import (
    Assessment,
    CalibrationSummary,
    ReviewHistory,
    ReviewRecord,
    ReviewSubmission,
)
from healteam_memory.resources import find_project_root


def artifacts() -> dict[str, str]:
    session_id = UUID("00000000-0000-4000-8000-000000000001")
    commands = {
        "create_session": CreateSessionCommand(
            client_request_id=UUID("00000000-0000-4000-8000-000000000002"),
            case_id="teen-truancy-01",
            school="satir",
        ),
        "submit_message": SubmitMessageCommand(
            client_message_id=UUID("00000000-0000-4000-8000-000000000003"),
            expected_turn=1,
            content="我想先听听每个人对这次见面的想法。",
        ),
        "session_snapshot": SessionSnapshot(
            session_id=session_id,
            case_id="teen-truancy-01",
            case_version="1.0.0",
            school="satir",
            state="awaiting_input",
        ),
        "utterance": EventEnvelope(
            event_id=UUID("00000000-0000-4000-8000-000000000004"),
            session_id=session_id,
            seq=1,
            round=1,
            type="utterance",
            actor="trainee",
            source="human",
            visibility="public",
            timestamp="2026-11-04T00:00:00Z",
            payload={"content": "我想先听听每个人对这次见面的想法。"},
        ),
    }
    schemas = {
        "create-session": CreateSessionCommand,
        "submit-message": SubmitMessageCommand,
        "session-snapshot": SessionSnapshot,
        "event-envelope": EventEnvelope,
        "case-definition": CaseDefinition,
        "session-view": SessionView,
        "session-history": SessionHistory,
        "message-receipt": MessageReceipt,
        "workflow-report": WorkflowReport,
        "usage-summary": UsageSummary,
        "assessment": Assessment,
        "review-submission": ReviewSubmission,
        "review-record": ReviewRecord,
        "review-history": ReviewHistory,
        "calibration-summary": CalibrationSummary,
    }
    result = {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        for name, model in schemas.items()
    }
    result["examples.json"] = (
        json.dumps(
            {
                "contract_version": CONTRACT_VERSION,
                "implementation_stage": "S4: trial evaluation and append-only human review",
                "examples": {name: model.model_dump(mode="json") for name, model in commands.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail instead of changing stale exports")
    args = parser.parse_args()
    destination = find_project_root() / "docs" / "contracts"
    stale: list[str] = []
    for name, content in artifacts().items():
        path = destination / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            destination.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        raise SystemExit("Contract exports are stale: " + ", ".join(stale))
    print("Contract schemas and examples are current" if args.check else "Exported session contracts")


if __name__ == "__main__":
    main()
