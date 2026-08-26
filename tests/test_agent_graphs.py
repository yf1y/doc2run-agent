import json

import pytest

from doc2run_agent.generation_agent import build_generation_agent_graph
from doc2run_agent.fix_agent import build_fix_agent_graph
from doc2run_agent.knowledge_tools import KnowledgeSearchTool
from doc2run_agent.retriever import LocalKnowledgeBase
from doc2run_agent.schemas import TaskSpec

from conftest import (
    FakeModel,
    fix_plan_response,
    implementation_plan_response,
    patch_response,
    patch_review_response,
    plan_review_response,
)


def make_tool(tmp_path):
    (tmp_path / "api.md").write_text("Use json.dumps to serialize output.", encoding="utf-8")
    return KnowledgeSearchTool(LocalKnowledgeBase.from_directory(tmp_path))


def task_spec():
    return TaskSpec(
        objective="Print a JSON result",
        outputs=[{"name": "result", "format": "JSON", "destination": "stdout"}],
        acceptance_criteria=["stdout is valid JSON"],
        status="confirmed",
        version=1,
    ).model_dump(mode="json")


def test_generation_agent_subgraph_retrieves_generates_and_validates(tmp_path):
    model = FakeModel(
        [
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "import json\nprint(json.dumps({'ok': True}))",
        ]
    )
    graph = build_generation_agent_graph(model, make_tool(tmp_path))

    result = graph.invoke({"task_spec": task_spec()})

    assert result["code_validation"]["ok"] is True
    assert result["retrieved_context"]
    assert {
        "plan_retrieval",
        "search_knowledge",
        "create_implementation_plan",
        "review_implementation_plan",
        "generate_code",
        "validate_code",
    }.issubset(graph.get_graph().nodes)
    assert result["implementation_plan"]["steps"]
    assert len(result["context_records"]) == 4


def test_fix_agent_subgraph_classifies_retrieves_repairs_and_validates(tmp_path):
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
    assert result["fix_attempts"] == 1
    assert result["code_validation"]["ok"] is True
    assert result["code"] == "print('fixed')\n"


def test_generation_agent_searches_again_when_plan_review_finds_a_gap(tmp_path):
    model = FakeModel(
        [
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(ok=False, queries=["json.dumps exact signature"]),
            implementation_plan_response(),
            plan_review_response(),
            "print('{}')",
        ]
    )
    graph = build_generation_agent_graph(model, make_tool(tmp_path))

    result = graph.invoke({"task_spec": task_spec()})

    assert result["additional_retrieval_queries"] == ["json.dumps exact signature"]
    assert result["additional_context"]
    assert result["plan_review"]["ok"] is True
    assert len(result["context_records"]) == 6


@pytest.mark.parametrize(
    "final_review",
    [
        plan_review_response(ok=False),
        plan_review_response(ok=True, queries=["one more unresolved API detail"]),
    ],
)
def test_generation_agent_stops_when_final_plan_review_is_not_ready(tmp_path, final_review):
    model = FakeModel(
        [
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(ok=False, queries=["json.dumps exact signature"]),
            implementation_plan_response(),
            final_review,
        ]
    )
    graph = build_generation_agent_graph(model, make_tool(tmp_path))

    result = graph.invoke({"task_spec": task_spec()})

    assert result["status"] == "plan_rejected"
    assert not (
        result["plan_review"]["ok"] and not result["plan_review"]["search_queries"]
    )
    assert "code" not in result
    assert len(model.calls) == 5


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
