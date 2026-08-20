import json

import pytest

from code_agent.requirements_agent import RequirementsAgent
from code_agent.schemas import SessionRecord

from conftest import FakeModel


def test_requirements_agent_collects_at_most_two_questions():
    response = json.dumps(
        {
            "spec_patch": {"objective": "Create a CSV report"},
            "confirmed_sections": ["goal"],
            "questions": ["Input?", "Output path?", "Extra question?"],
            "assistant_message": "I understand the goal.",
        }
    )
    record = RequirementsAgent(FakeModel([response])).process(
        SessionRecord(session_id="demo"),
        "Create a CSV report",
    )

    assert record.phase == "collecting_inputs_outputs"
    assert record.pending_questions == ["Input?", "Output path?"]
    assert record.confirmed_sections == ["goal"]


def test_requirements_agent_reaches_confirmation_only_when_complete():
    response = json.dumps(
        {
            "spec_patch": {
                "objective": "Create a CSV report",
                "inputs": [{"name": "records", "type": "JSON", "source": "input.json"}],
                "outputs": [{"name": "report", "format": "CSV", "destination": "report.csv"}],
                "constraints": ["Use the documented SDK"],
                "allowed_apis": ["records_api"],
                "side_effects": ["Write report.csv"],
                "acceptance_criteria": ["The CSV contains a header and one row per category"],
            },
            "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
            "questions": [],
            "assistant_message": "The specification is ready.",
        }
    )
    record = RequirementsAgent(FakeModel([response])).process(
        SessionRecord(session_id="demo"),
        "Here are all details",
    )

    assert record.phase == "awaiting_confirmation"
    assert record.draft_spec.status == "ready_for_confirmation"
    assert record.pending_questions == []


def test_requirements_agent_rejects_unknown_spec_fields():
    response = json.dumps(
        {
            "spec_patch": {"objective": "Do work", "secret_path": "/tmp"},
            "confirmed_sections": ["goal"],
            "questions": [],
            "assistant_message": "",
        }
    )

    with pytest.raises(ValueError, match="unknown TaskSpec fields"):
        RequirementsAgent(FakeModel([response])).process(
            SessionRecord(session_id="demo"),
            "Do work",
        )
