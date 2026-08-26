import json

import pytest

from doc2run_agent.cli import run_chat
from doc2run_agent.session_store import FileSessionStore

from conftest import FakeModel, implementation_plan_response, plan_review_response


def completed_spec_response():
    return json.dumps(
        {
            "spec_patch": {
                "objective": "Print a JSON result",
                "inputs": [],
                "outputs": [{"name": "result", "format": "JSON", "destination": "stdout"}],
                "constraints": ["Use the standard library"],
                "allowed_dependencies": ["standard-library"],
                "allowed_apis": [],
                "side_effects": [],
                "acceptance_criteria": ["stdout is valid JSON"],
            },
            "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
            "questions": [],
            "assistant_message": "Ready for confirmation.",
        }
    )


def test_interactive_cli_persists_and_executes_a_session(tmp_path):
    knowledge = tmp_path / "knowledge"
    api = knowledge / "api"
    api.mkdir(parents=True)
    (api / "json.md").write_text("Use json.dumps for JSON output.", encoding="utf-8")
    inputs = iter(["Print JSON", "/confirm", "/exit"])
    outputs = []

    run_chat(
        FakeModel(
            [
                completed_spec_response(),
                json.dumps({"queries": ["JSON output"]}),
                implementation_plan_response(),
                plan_review_response(),
                "import json\nprint(json.dumps({'ok': True}))",
            ]
        ),
        session_id="cli-demo",
        sessions_directory=tmp_path / "sessions",
        knowledge_directory=knowledge,
        input_fn=lambda _: next(inputs),
        output_fn=outputs.append,
        timeout_seconds=1,
    )

    record = FileSessionStore(tmp_path / "sessions").load_or_create("cli-demo")
    assert record.status == "awaiting_review"
    assert record.run_result is not None and record.run_result.ok
    assert any("Enter /confirm" in value for value in outputs)
    assert any(f"API documentation: {api}" in value for value in outputs)
    assert any("Domain documentation and approved scenario memory: disabled" in value for value in outputs)
    assert any('"ok": true' in value for value in outputs)
    assert outputs[-1] == "Session saved. Bye."


def test_cli_rejects_api_documents_in_the_wrong_directory(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "api.md").write_text("An API document in the wrong place.", encoding="utf-8")

    with pytest.raises(ValueError, match="Put API material under"):
        run_chat(
            FakeModel([]),
            session_id="wrong-layout",
            sessions_directory=tmp_path / "sessions",
            knowledge_directory=knowledge,
            memory_directory=tmp_path / "memory",
            input_fn=lambda _: "/exit",
        )


def test_cli_rejects_an_unfilled_api_template(tmp_path):
    api = tmp_path / "knowledge" / "api"
    api.mkdir(parents=True)
    (api / "reference.md").write_text(
        "<!-- Replace this with the real API reference. -->", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Replace the template comments"):
        run_chat(
            FakeModel([]),
            session_id="empty-template",
            sessions_directory=tmp_path / "sessions",
            knowledge_directory=tmp_path / "knowledge",
            memory_directory=tmp_path / "memory",
            input_fn=lambda _: "/exit",
        )


def test_cli_reports_separate_domain_document_and_memory_locations(tmp_path):
    knowledge = tmp_path / "knowledge"
    api = knowledge / "api"
    domain = knowledge / "domains" / "power"
    domain_docs = domain / "docs"
    api.mkdir(parents=True)
    domain_docs.mkdir(parents=True)
    (api / "reference.md").write_text("create_node(name: str)", encoding="utf-8")
    (domain_docs / "rules.md").write_text("A feeder must remain connected.", encoding="utf-8")
    (domain / "memory_schema.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "scenario_kind": "power_network",
                "required_fields": ["nodes"],
                "allowed_fields": ["nodes"],
                "field_types": {"nodes": "array"},
                "forbidden_keys": [],
            }
        ),
        encoding="utf-8",
    )
    outputs = []
    memory = tmp_path / "memory"

    run_chat(
        FakeModel([]),
        session_id="domain-layout",
        sessions_directory=tmp_path / "sessions",
        knowledge_directory=knowledge,
        memory_directory=memory,
        domain="power",
        input_fn=lambda _: "/exit",
        output_fn=outputs.append,
    )

    assert any(f"Domain documentation (power): {domain_docs} [loaded]" in value for value in outputs)
    assert any(f"Approved scenario memory: {memory / 'approved' / 'power'}" in value for value in outputs)
