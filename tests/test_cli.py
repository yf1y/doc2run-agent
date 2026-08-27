"""Tests for interactive CLI session orchestration and persistence."""

import json

import pytest

from doc2run_agent.cli import (
    build_parser,
    confirm_named_session,
    run_chat,
    select_session,
)
from doc2run_agent.knowledge.tools import KnowledgeSearchTool
from doc2run_agent.storage.sessions import FileSessionStore

from conftest import FakeModel, scenario_plan_text


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
            "scenario_plan": scenario_plan_text(),
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
    assert any("Scene library:" in value for value in outputs)
    assert any('"ok": true' in value for value in outputs)
    assert outputs[-1] == "Session saved. Bye."


def test_cli_accepts_repeated_runtime_environment_options():
    args = build_parser().parse_args(
        ["--runtime-env", "SDK_API_TOKEN", "--runtime-env", "SDK_ENDPOINT"]
    )

    assert args.runtime_env == ["SDK_API_TOKEN", "SDK_ENDPOINT"]


def test_cli_omits_session_to_enable_interactive_selection():
    assert build_parser().parse_args([]).session is None
    assert build_parser().parse_args(["--session", "friend-test-01"]).session == (
        "friend-test-01"
    )


def test_session_selector_lists_only_names_and_continues_selected_session(tmp_path):
    sessions = tmp_path / "sessions"
    store = FileSessionStore(sessions)
    store.load_or_create("alpha")
    store.load_or_create("friend-test-01")
    outputs = []

    selected = select_session(
        sessions,
        input_fn=lambda _: "2",
        output_fn=outputs.append,
    )

    assert selected == "friend-test-01"
    assert "1. alpha" in outputs
    assert "2. friend-test-01" in outputs
    assert not any("phase" in value or "updated" in value for value in outputs)


def test_session_selector_can_create_a_new_named_session(tmp_path):
    selected = select_session(
        tmp_path / "sessions",
        input_fn=lambda _: "friend-test-02",
        output_fn=lambda _: None,
    )

    assert selected == "friend-test-02"
    assert not FileSessionStore(tmp_path / "sessions").has_session("friend-test-02")


def test_named_session_requires_confirmation_only_when_it_is_new(tmp_path):
    sessions = tmp_path / "sessions"
    store = FileSessionStore(sessions)
    store.load_or_create("existing")

    assert confirm_named_session(
        sessions,
        "existing",
        input_fn=lambda _: pytest.fail("existing sessions should not prompt"),
    ) == "existing"
    assert confirm_named_session(sessions, "new", input_fn=lambda _: "yes") == "new"
    assert confirm_named_session(
        sessions,
        "declined",
        input_fn=lambda _: "no",
        output_fn=lambda _: None,
    ) is None
    assert not store.has_session("new")


def test_cli_refreshes_directory_knowledge_before_confirmation(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    api = knowledge / "api"
    api.mkdir(parents=True)
    (api / "json.md").write_text("Use json.dumps for JSON output.", encoding="utf-8")
    calls = []
    original_refresh = KnowledgeSearchTool.refresh

    def record_refresh(tool):
        calls.append(tool.source_directory)
        original_refresh(tool)

    monkeypatch.setattr(KnowledgeSearchTool, "refresh", record_refresh)
    inputs = iter(["Print JSON", "/confirm", "/exit"])

    run_chat(
        FakeModel(
            [
                completed_spec_response(),
                json.dumps({"queries": ["JSON output"]}),
                "import json\nprint(json.dumps({'ok': True}))",
            ]
        ),
        session_id="refresh-demo",
        sessions_directory=tmp_path / "sessions",
        knowledge_directory=knowledge,
        input_fn=lambda _: next(inputs),
        output_fn=lambda _: None,
        timeout_seconds=1,
    )

    assert calls == [api]


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
            input_fn=lambda _: "/exit",
        )


def test_cli_reports_api_and_scene_locations(tmp_path):
    knowledge = tmp_path / "knowledge"
    api = knowledge / "api"
    scenes = knowledge / "scenes"
    api.mkdir(parents=True)
    scenes.mkdir(parents=True)
    (api / "reference.md").write_text("create_node(name: str)", encoding="utf-8")
    (scenes / "five.md").write_text("# 5 nodes\n\n- connected feeder", encoding="utf-8")
    outputs = []

    run_chat(
        FakeModel([]),
        session_id="scene-layout",
        sessions_directory=tmp_path / "sessions",
        knowledge_directory=knowledge,
        input_fn=lambda _: "/exit",
        output_fn=outputs.append,
    )

    assert any(f"API documentation: {api}" in value for value in outputs)
    assert any(f"Scene library: {scenes} [loaded]" in value for value in outputs)
