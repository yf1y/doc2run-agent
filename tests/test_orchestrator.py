import json

from doc2run_agent.knowledge_tools import KnowledgeSearchTool
from doc2run_agent.llm import AgentModels
from doc2run_agent.orchestrator import Doc2RunOrchestrator
from doc2run_agent.retriever import LocalKnowledgeBase
from doc2run_agent.runner import LocalPythonRunner
from doc2run_agent.session_store import FileSessionStore

from conftest import (
    FakeModel,
    fix_plan_response,
    implementation_plan_response,
    patch_response,
    patch_review_response,
    plan_review_response,
)


def complete_requirements_response():
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
            "assistant_message": "The specification is ready.",
        }
    )


def make_orchestrator(tmp_path, responses, max_fix_attempts=2):
    knowledge_directory = tmp_path / "knowledge"
    knowledge_directory.mkdir()
    (knowledge_directory / "api.md").write_text(
        "Use json.dumps to serialize a dictionary to stdout.", encoding="utf-8"
    )
    tool = KnowledgeSearchTool(LocalKnowledgeBase.from_directory(knowledge_directory))
    store = FileSessionStore(tmp_path / "sessions")
    orchestrator = Doc2RunOrchestrator(
        FakeModel(responses),
        tool,
        store,
        LocalPythonRunner(timeout_seconds=1),
        max_fix_attempts=max_fix_attempts,
    )
    return orchestrator, store


def test_orchestrator_waits_for_confirmation_then_runs_code(tmp_path):
    orchestrator, store = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "import json\nprint(json.dumps({'ok': True}))",
        ],
    )

    waiting = orchestrator.handle_message("demo", "Print a JSON result")
    result = orchestrator.confirm("demo")

    assert waiting["status"] == "awaiting_confirmation"
    assert result["status"] == "awaiting_review"
    assert result["run_result"]["stdout"].strip() == '{"ok": true}'
    assert (store.session_directory("demo") / "task_specs" / "task_spec_v1.json").exists()
    assert (store.session_directory("demo") / "runs" / "initial" / "run.json").exists()
    assert (store.session_directory("demo") / "planning" / "implementation_plan.json").exists()
    assert (store.session_directory("demo") / "planning" / "api_context.md").exists()
    assert (store.session_directory("demo") / "planning" / "generation_notes.md").exists()
    assert (store.session_directory("demo") / "contexts" / "manifest.json").exists()
    context_manifest = json.loads(
        (store.session_directory("demo") / "contexts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert {item["stage"] for item in context_manifest} >= {"requirements", "code_generation"}
    assert {"requirements_agent", "generation_agent", "fix_agent", "execute"}.issubset(
        orchestrator.graph.get_graph().nodes
    )


def test_orchestrator_repairs_failed_execution(tmp_path):
    orchestrator, store = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "raise RuntimeError('broken')",
            fix_plan_response(),
            patch_response(
                "raise RuntimeError('broken')",
                "import json\nprint(json.dumps({'fixed': True}))",
            ),
            patch_review_response(),
        ],
    )

    orchestrator.handle_message("repair-demo", "Print a JSON result")
    result = orchestrator.confirm("repair-demo")

    assert result["status"] == "awaiting_review"
    assert result["fix_attempts"] == 1
    assert len(result["run_history"]) == 2
    assert (store.session_directory("repair-demo") / "runs" / "fix_001" / "run.json").exists()
    assert (store.session_directory("repair-demo") / "runs" / "fix_001" / "fix_plan.json").exists()
    assert (store.session_directory("repair-demo") / "runs" / "fix_001" / "code_patch.json").exists()
    assert (store.session_directory("repair-demo") / "runs" / "fix_001" / "patch_review.json").exists()


def test_orchestrator_routes_each_stage_to_its_configured_model(tmp_path):
    knowledge_directory = tmp_path / "knowledge"
    knowledge_directory.mkdir()
    (knowledge_directory / "api.md").write_text(
        "Use json.dumps to serialize a dictionary to stdout.", encoding="utf-8"
    )
    requirements_model = FakeModel([complete_requirements_response()])
    code_model = FakeModel(
        [
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "raise RuntimeError('broken')",
        ]
    )
    fix_model = FakeModel(
        [
            fix_plan_response(),
            patch_response("raise RuntimeError('broken')", "print('{}')"),
            patch_review_response(),
        ]
    )
    store = FileSessionStore(tmp_path / "sessions")
    orchestrator = Doc2RunOrchestrator(
        AgentModels(requirements_model, code_model, fix_model),
        KnowledgeSearchTool(LocalKnowledgeBase.from_directory(knowledge_directory)),
        store,
        LocalPythonRunner(timeout_seconds=1),
    )

    orchestrator.handle_message("multi-model", "Print a JSON result")
    result = orchestrator.confirm("multi-model")

    assert result["status"] == "awaiting_review"
    assert len(requirements_model.calls) == 1
    assert len(code_model.calls) == 4
    assert len(fix_model.calls) == 3


def test_orchestrator_rejects_confirmation_before_requirements_are_complete(tmp_path):
    orchestrator, _ = make_orchestrator(tmp_path, [])

    try:
        orchestrator.confirm("too-early")
    except ValueError as error:
        assert "not awaiting confirmation" in str(error)
    else:
        raise AssertionError("Expected confirmation gate to reject an incomplete TaskSpec")


def test_orchestrator_stops_at_repair_limit(tmp_path):
    orchestrator, store = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "raise RuntimeError('initial')",
            fix_plan_response(),
            patch_response(
                "raise RuntimeError('initial')", "raise RuntimeError('still broken')"
            ),
            patch_review_response(),
        ],
        max_fix_attempts=1,
    )

    orchestrator.handle_message("failed-demo", "Print a JSON result")
    result = orchestrator.confirm("failed-demo")

    assert result["status"] == "failed"
    assert result["fix_attempts"] == 1
    assert len(result["run_history"]) == 2
    assert store.load_or_create("failed-demo").status == "failed"


def test_orchestrator_persists_rejected_final_plan_without_running_code(tmp_path):
    orchestrator, store = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(ok=False, queries=["json.dumps exact signature"]),
            implementation_plan_response(),
            plan_review_response(ok=False),
        ],
    )
    orchestrator.handle_message("plan-blocked", "Print a JSON result")

    result = orchestrator.confirm("plan-blocked")

    assert result["status"] == "plan_rejected"
    assert "code" not in result
    assert not (store.session_directory("plan-blocked") / "runs" / "initial").exists()
    assert (store.session_directory("plan-blocked") / "planning" / "plan_review.json").exists()
    assert store.load_or_create("plan-blocked").phase == "generating_code"


def test_orchestrator_retries_an_interrupted_generation_from_confirmed_snapshot(tmp_path):
    orchestrator, store = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "print('{}')",
        ],
    )
    orchestrator.handle_message("resume-demo", "Print a JSON result")
    record = store.load_or_create("resume-demo")
    snapshot = store.snapshot_confirmed_spec(record)
    record.phase = "executing"
    record.status = "executing"
    store.save(record)

    result = orchestrator.confirm("resume-demo")

    assert result["status"] == "awaiting_review"
    assert result["task_spec"]["version"] == snapshot.version
    assert len(list((store.session_directory("resume-demo") / "task_specs").glob("*.json"))) == 1


def test_orchestrator_refines_working_code_then_runs_it_again(tmp_path):
    orchestrator, _ = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "print('{}')",
            fix_plan_response(),
            patch_response("print('{}')", "print('{\"changed\": true}')"),
            patch_review_response(),
        ],
    )
    orchestrator.handle_message("terminal-demo", "Print a JSON result")
    orchestrator.confirm("terminal-demo")

    result = orchestrator.handle_message("terminal-demo", "Keep JSON but include changed=true")

    assert result["status"] == "awaiting_review"
    assert json.loads(result["run_result"]["stdout"]) == {"changed": True}
    assert result["fix_attempts"] == 1
    assert len(result["run_history"]) == 2


def test_each_user_refinement_gets_its_own_fix_budget(tmp_path):
    orchestrator, _ = make_orchestrator(
        tmp_path,
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "print('{}')",
            fix_plan_response(),
            patch_response("print('{}')", "print('{\"version\": 1}')"),
            patch_review_response(),
            fix_plan_response(),
            patch_response(
                "print('{\"version\": 1}')", "print('{\"version\": 2}')"
            ),
            patch_review_response(),
        ],
        max_fix_attempts=1,
    )
    orchestrator.handle_message("two-refinements", "Print a JSON result")
    orchestrator.confirm("two-refinements")

    first = orchestrator.handle_message("two-refinements", "Set version to 1")
    second = orchestrator.handle_message("two-refinements", "Set version to 2")

    assert first["status"] == "awaiting_review"
    assert second["status"] == "awaiting_review"
    assert json.loads(second["run_result"]["stdout"]) == {"version": 2}
    assert second["fix_attempts"] == 2
