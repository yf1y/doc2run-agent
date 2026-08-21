import json

from doc2run_agent.cli import run_chat
from doc2run_agent.session_store import FileSessionStore

from conftest import FakeModel


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
    knowledge.mkdir()
    (knowledge / "json.md").write_text("Use json.dumps for JSON output.", encoding="utf-8")
    inputs = iter(["Print JSON", "/confirm", "/exit"])
    outputs = []

    run_chat(
        FakeModel(
            [
                completed_spec_response(),
                json.dumps({"queries": ["JSON output"]}),
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
    assert record.status == "succeeded"
    assert record.run_result is not None and record.run_result.ok
    assert any("Enter /confirm" in value for value in outputs)
    assert any('"ok": true' in value for value in outputs)
    assert outputs[-1] == "Session saved. Bye."
