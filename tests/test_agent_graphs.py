"""Tests for the isolated Code and Fix stage graphs."""

import json

from doc2run_agent.agents.fix import build_fix_agent_graph
from doc2run_agent.agents.code import build_code_agent_graph
from doc2run_agent.knowledge.retriever import LocalKnowledgeBase
from doc2run_agent.knowledge.tools import KnowledgeSearchTool
from doc2run_agent.schemas import TaskSpec

from conftest import FakeModel, fix_plan_response, patch_response, patch_review_response, scenario_plan_text


def make_tool(tmp_path):
    (tmp_path / "api.md").write_text(
        "Use json.dumps(value) to serialize output.", encoding="utf-8"
    )
    return KnowledgeSearchTool(LocalKnowledgeBase.from_directory(tmp_path))


def task_spec():
    return TaskSpec(
        objective="Print a JSON result",
        outputs=[{"name": "result", "format": "JSON", "destination": "stdout"}],
        acceptance_criteria=["stdout is valid JSON"],
        status="confirmed",
        version=1,
    ).model_dump(mode="json")


def test_code_searches_api_from_confirmed_plan_then_generates(tmp_path):
    model = FakeModel(
        [
            json.dumps({"queries": ["json.dumps serialization"]}),
            "import json\nprint(json.dumps({'ok': True}))",
        ]
    )
    graph = build_code_agent_graph(model, make_tool(tmp_path))

    result = graph.invoke(
        {"task_spec": task_spec(), "scenario_plan": scenario_plan_text(), "decisions": []}
    )

    assert result["code_validation"]["ok"] is True
    assert result["retrieved_context"]
    assert {
        "plan_api_retrieval",
        "search_api_knowledge",
        "generate_code",
        "validate_code",
    }.issubset(graph.get_graph().nodes)
    assert scenario_plan_text() in model.calls[0][1]
    assert scenario_plan_text() in model.calls[1][1]
    assert "json.dumps" in model.calls[1][1]
    assert len(result["context_records"]) == 2


def test_code_does_not_retrieve_scene_knowledge_in_code_stage(tmp_path):
    model = FakeModel(
        [json.dumps({"queries": ["create_node"]}), "print('{}')"]
    )
    graph = build_code_agent_graph(model, make_tool(tmp_path))

    result = graph.invoke(
        {"task_spec": task_spec(), "scenario_plan": "# 5 节点\n\n- 节点 1 到 5 顺序连接"}
    )

    assert all(str(item["source"]).endswith("api.md#1.1") for item in result["retrieved_context"])
    assert "Selected Scene" not in model.calls[1][1]


def test_code_accepts_sdk_import_only_when_api_context_documents_it(tmp_path):
    (tmp_path / "sdk.md").write_text(
        "from private_sdk import Client\n\nClient() creates a client.", encoding="utf-8"
    )
    model = FakeModel(
        [
            json.dumps({"queries": ["private_sdk Client"]}),
            "from private_sdk import Client\nprint(Client)",
        ]
    )
    graph = build_code_agent_graph(
        model, KnowledgeSearchTool(LocalKnowledgeBase.from_directory(tmp_path))
    )

    result = graph.invoke(
        {"task_spec": task_spec(), "scenario_plan": scenario_plan_text()}
    )

    assert result["code_validation"]["ok"] is True


def test_fix_agent_retrieves_api_and_preserves_confirmed_plan(tmp_path):
    model = FakeModel(
        [
            fix_plan_response(),
            patch_response("raise RuntimeError('broken')", "print('fixed')"),
            patch_review_response(),
        ]
    )
    graph = build_fix_agent_graph(model, make_tool(tmp_path))
    state = {
        "task_spec": task_spec(),
        "scenario_plan": scenario_plan_text(),
        "code": "raise RuntimeError('broken')",
        "code_validation": {"ok": True, "errors": [], "imports": []},
        "run_result": {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "RuntimeError: broken",
            "timed_out": False,
            "duration_seconds": 0.1,
        },
        "retrieved_context": [],
        "fix_attempts": 0,
    }

    result = graph.invoke(state)

    assert result["error_info"]["category"] == "runtime_error"
    assert result["code_validation"]["ok"] is True
    assert result["code"] == "print('fixed')\n"
    assert scenario_plan_text() in model.calls[0][1]
    assert scenario_plan_text() in model.calls[1][1]


def test_fix_agent_does_not_run_a_patch_rejected_by_review(tmp_path):
    model = FakeModel(
        [
            fix_plan_response(),
            patch_response("raise RuntimeError('broken')", "print('fixed')"),
            patch_review_response(ok=False),
        ]
    )
    graph = build_fix_agent_graph(model, make_tool(tmp_path))
    state = {
        "task_spec": task_spec(),
        "scenario_plan": scenario_plan_text(),
        "code": "raise RuntimeError('broken')",
        "code_validation": {"ok": True, "errors": [], "imports": []},
        "run_result": {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "RuntimeError: broken",
            "timed_out": False,
            "duration_seconds": 0.1,
        },
        "retrieved_context": [],
        "fix_attempts": 0,
    }

    result = graph.invoke(state)

    assert result["code_validation"]["ok"] is False
    assert "patch is incorrect" in result["code_validation"]["errors"][0]
