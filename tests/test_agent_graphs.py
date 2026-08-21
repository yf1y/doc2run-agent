import json

from doc2run_agent.generation_agent import build_generation_agent_graph
from doc2run_agent.fix_agent import build_fix_agent_graph
from doc2run_agent.knowledge_tools import KnowledgeSearchTool
from doc2run_agent.retriever import LocalKnowledgeBase
from doc2run_agent.schemas import TaskSpec

from conftest import FakeModel


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
    model = FakeModel([json.dumps({"queries": ["JSON serialization"]}), "import json\nprint(json.dumps({'ok': True}))"])
    graph = build_generation_agent_graph(model, make_tool(tmp_path))

    result = graph.invoke({"task_spec": task_spec()})

    assert result["code_validation"]["ok"] is True
    assert result["retrieved_context"]
    assert {"plan_retrieval", "search_knowledge", "generate_code", "validate_code"}.issubset(
        graph.get_graph().nodes
    )


def test_fix_agent_subgraph_classifies_retrieves_repairs_and_validates(tmp_path):
    model = FakeModel([json.dumps({"queries": ["RuntimeError fix"]}), "print('fixed')"])
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
