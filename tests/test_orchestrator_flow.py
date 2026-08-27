"""End-to-end logic tests for the Chat-Code-Fix-Memory workflow."""

import json

import pytest

from doc2run_agent.knowledge.retriever import LocalKnowledgeBase
from doc2run_agent.knowledge.scenes import SceneLibrary
from doc2run_agent.knowledge.tools import KnowledgeSearchTool, SceneSearchTool
from doc2run_agent.runtime.runner import LocalPythonRunner
from doc2run_agent.storage.sessions import FileSessionStore
from doc2run_agent.workflow.orchestrator import Doc2RunOrchestrator

from conftest import FakeModel, fix_plan_response, patch_response, patch_review_response


PLAN = (
    "# 场景目标\n\n生成 5 节点网络。\n\n"
    "# 器件清单\n\n- 5 个节点\n\n"
    "# 排布与连接关系\n\n- 1-2-3-4-5\n\n"
    "# 输出与验收标准\n\n- stdout 是有效 JSON"
)


def complete_chat_response():
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
            "assistant_message": "The specification and Scenario Plan are ready.",
            "scenario_plan": PLAN,
        }
    )


def make_orchestrator(tmp_path, responses, max_fix_attempts=2):
    api = tmp_path / "domain_knowledge" / "api"
    scenes = tmp_path / "domain_knowledge" / "scenes"
    api.mkdir(parents=True)
    scenes.mkdir(parents=True)
    (api / "api.md").write_text(
        "Use json.dumps(value) to serialize a dictionary to stdout.", encoding="utf-8"
    )
    (scenes / "five.md").write_text(
        "# 5 节点参考\n\n- 五个节点按 1-2-3-4-5 连接\n", encoding="utf-8"
    )
    store = FileSessionStore(tmp_path / "sessions")
    model = FakeModel(responses)
    orchestrator = Doc2RunOrchestrator(
        model,
        KnowledgeSearchTool(LocalKnowledgeBase.from_directory(api)),
        store,
        LocalPythonRunner(timeout_seconds=1),
        max_fix_attempts=max_fix_attempts,
        scene_tool=SceneSearchTool.from_directory(scenes),
        scene_library=SceneLibrary(scenes),
    )
    return orchestrator, store, model, scenes


def test_orchestrator_passes_confirmed_plan_unchanged_to_code_and_runs(tmp_path):
    orchestrator, store, model, _ = make_orchestrator(
        tmp_path,
        [
            complete_chat_response(),
            json.dumps({"queries": ["json.dumps serialization"]}),
            "import json\nprint(json.dumps({'ok': True}))",
        ],
    )

    waiting = orchestrator.handle_message("demo", "按照 5 节点场景生成网络")
    result = orchestrator.confirm("demo")

    assert waiting["status"] == "awaiting_confirmation"
    assert result["status"] == "awaiting_review"
    assert json.loads(result["run_result"]["stdout"]) == {"ok": True}
    record = store.load_or_create("demo")
    assert record.confirmed_plan == PLAN
    assert PLAN in model.calls[1][1]
    assert PLAN in model.calls[2][1]
    assert (store.session_directory("demo") / "planning" / "selected_scene.md").exists()
    assert (store.session_directory("demo") / "planning" / "confirmed_plan.md").read_text(
        encoding="utf-8"
    ).strip() == PLAN


def test_orchestrator_repairs_with_same_confirmed_plan(tmp_path):
    orchestrator, _, model, _ = make_orchestrator(
        tmp_path,
        [
            complete_chat_response(),
            json.dumps({"queries": ["json.dumps serialization"]}),
            "raise RuntimeError('broken')",
            fix_plan_response(),
            patch_response("raise RuntimeError('broken')", "print('{}')"),
            patch_review_response(),
        ],
    )

    orchestrator.handle_message("repair", "按照 5 节点场景生成网络")
    result = orchestrator.confirm("repair")

    assert result["status"] == "awaiting_review"
    assert result["fix_attempts"] == 1
    assert PLAN in model.calls[3][1]
    assert PLAN in model.calls[4][1]


def test_approve_saves_confirmed_plan_directly_as_scene(tmp_path):
    orchestrator, _, _, scenes = make_orchestrator(
        tmp_path,
        [
            complete_chat_response(),
            json.dumps({"queries": ["json.dumps serialization"]}),
            "print('{}')",
        ],
    )
    orchestrator.handle_message("approve", "按照 5 节点场景生成网络")
    orchestrator.confirm("approve")

    result = orchestrator.approve("approve", "verified")

    assert result["status"] == "memory"
    saved = scenes / result["scene_path"].split("/")[-1]
    assert saved.read_text(encoding="utf-8").strip() == PLAN
    assert not (tmp_path / "memory").exists()


def test_refinement_cannot_silently_change_the_confirmed_contract(tmp_path):
    orchestrator, store, model, _ = make_orchestrator(
        tmp_path,
        [
            complete_chat_response(),
            json.dumps({"queries": ["json.dumps serialization"]}),
            "print('{}')",
            json.dumps(
                {
                    "problem": "The user requested a different output contract",
                    "location": "confirmed output and acceptance criteria",
                    "change": "A new TaskSpec and Scenario Plan are required",
                    "keep_unchanged": ["the currently verified JSON version"],
                    "search_queries": [],
                    "contract_compatible": False,
                }
            ),
        ],
    )
    orchestrator.handle_message("contract", "按照 5 节点场景生成网络")
    orchestrator.confirm("contract")

    result = orchestrator.handle_message("contract", "把输出改成 CSV，并写入绝对路径")

    assert result["status"] == "awaiting_review"
    assert "working version was left unchanged" in result["assistant_message"]
    record = store.load_or_create("contract")
    assert record.generated_code == "print('{}')\n"
    assert record.confirmed_plan == PLAN
    assert record.fix_attempts == 0
    assert len(model.calls) == 4
    assert (
        store.session_directory("contract") / "planning" / "refinement_conflict.json"
    ).exists()


def test_orchestrator_rejects_confirmation_before_plan_is_ready(tmp_path):
    orchestrator, _, _, _ = make_orchestrator(tmp_path, [])

    with pytest.raises(ValueError, match="not awaiting confirmation"):
        orchestrator.confirm("too-early")
