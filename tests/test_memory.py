import json

from doc2run_agent.knowledge_tools import KnowledgeSearchTool
from doc2run_agent.generation_agent import build_generation_agent_graph
from doc2run_agent.memory_store import ScenarioMemoryStore
from doc2run_agent.orchestrator import Doc2RunOrchestrator
from doc2run_agent.retriever import LocalKnowledgeBase
from doc2run_agent.runner import LocalPythonRunner
from doc2run_agent.schemas import MemoryReview, ScenarioCandidate
from doc2run_agent.session_store import FileSessionStore

from conftest import (
    FakeModel,
    fix_plan_response,
    implementation_plan_response,
    patch_response,
    patch_review_response,
    plan_review_response,
)
from test_orchestrator import complete_requirements_response
from test_agent_graphs import task_spec


def make_memory_store(tmp_path):
    schema_directory = tmp_path / "knowledge" / "domains" / "power"
    schema_directory.mkdir(parents=True)
    (schema_directory / "memory_schema.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "scenario_kind": "power_network",
                "required_fields": ["nodes", "connections"],
                "allowed_fields": ["nodes", "connections", "loads", "parameters"],
                "field_types": {
                    "nodes": "array",
                    "connections": "array",
                    "loads": "array",
                    "parameters": "object",
                },
                "forbidden_keys": ["endpoint"],
            }
        ),
        encoding="utf-8",
    )
    return ScenarioMemoryStore(tmp_path / "memory", tmp_path / "knowledge" / "domains")


def test_memory_store_blocks_api_fields_and_retrieves_only_approved_domain(tmp_path):
    store = make_memory_store(tmp_path)
    schema = store.load_schema("power")
    invalid = ScenarioCandidate(
        scenario_kind="power_network",
        scenario_name="bad",
        summary="contains an interface detail",
        data={"nodes": [], "connections": [], "api": "create_node"},
    )
    assert any("forbidden" in item or "not allowed" in item for item in store.validate_candidate(invalid, schema))

    candidate = ScenarioCandidate(
        scenario_kind="power_network",
        scenario_name="33-node feeder",
        summary="A reviewed 33-node network layout",
        data={"nodes": [1, 2, 3], "connections": [[1, 2], [2, 3]]},
    )
    candidate_id, _ = store.save_candidate(
        session_id="source",
        domain="power",
        candidate=candidate,
        validation_errors=[],
        review=MemoryReview(ok=True, summary="supported"),
        approval_note="looks correct",
    )
    store.approve("power", candidate_id)

    assert store.search("power", "33-node network")
    assert store.search("another_domain", "33-node network") == []


def test_approve_uses_fresh_memory_context_then_requires_remember(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "api.md").write_text("Use json.dumps for JSON output.", encoding="utf-8")
    memory = make_memory_store(tmp_path)
    model = FakeModel(
        [
            complete_requirements_response(),
            json.dumps({"queries": ["JSON serialization"]}),
            implementation_plan_response(),
            plan_review_response(),
            "raise RuntimeError('broken')",
            fix_plan_response(),
            patch_response("raise RuntimeError('broken')", "print('{}')"),
            patch_review_response(),
            json.dumps(
                {
                    "scenario_kind": "power_network",
                    "scenario_name": "33-node feeder",
                    "summary": "Approved network data",
                    "data": {"nodes": [1, 2], "connections": [[1, 2]]},
                }
            ),
            json.dumps({"ok": True, "problems": [], "summary": "supported by final result"}),
        ]
    )
    store = FileSessionStore(tmp_path / "sessions")
    orchestrator = Doc2RunOrchestrator(
        model,
        KnowledgeSearchTool(LocalKnowledgeBase.from_directory(knowledge)),
        store,
        LocalPythonRunner(timeout_seconds=1),
        scenario_memory=memory,
        domain="power",
    )

    orchestrator.handle_message("memory-demo", "Print a JSON result")
    assert orchestrator.confirm("memory-demo")["status"] == "awaiting_review"
    approved_code = orchestrator.approve("memory-demo", "The 33-node result is correct")

    assert approved_code["status"] == "memory_candidate_ready"
    extraction_system, extraction_prompt = model.calls[-2]
    review_system, review_prompt = model.calls[-1]
    assert "scenario data" in extraction_system
    assert "raise RuntimeError('broken')" not in extraction_prompt
    assert "independently review" in review_system.lower()
    assert "raise RuntimeError('broken')" not in review_prompt
    assert "Candidate (review only" in review_prompt
    assert not list((tmp_path / "memory" / "approved" / "power").glob("*/scenario.json"))

    remembered = orchestrator.remember("memory-demo")
    assert remembered["status"] == "approved"
    assert list((tmp_path / "memory" / "approved" / "power").glob("*/scenario.json"))
    assert not list((tmp_path / "memory" / "candidates" / "power").glob("*/scenario.json"))


def test_generation_receives_approved_scenarios_in_a_separate_domain_context(tmp_path):
    memory = make_memory_store(tmp_path)
    candidate = ScenarioCandidate(
        scenario_kind="power_network",
        scenario_name="33-node feeder",
        summary="A reusable network layout",
        data={"nodes": [1, 2], "connections": [[1, 2]]},
    )
    candidate_id, _ = memory.save_candidate(
        session_id="prior",
        domain="power",
        candidate=candidate,
        validation_errors=[],
        review=MemoryReview(ok=True, summary="supported"),
        approval_note="approved",
    )
    memory.approve("power", candidate_id)
    api_directory = tmp_path / "api"
    api_directory.mkdir()
    (api_directory / "sdk.md").write_text("Use json.dumps for JSON output.", encoding="utf-8")
    model = FakeModel(
        [
            json.dumps({"queries": ["JSON power network"]}),
            implementation_plan_response(),
            plan_review_response(),
            "print('{}')",
        ]
    )
    graph = build_generation_agent_graph(
        model,
        KnowledgeSearchTool(LocalKnowledgeBase.from_directory(api_directory)),
        scenario_memory=memory,
        domain="power",
    )

    result = graph.invoke({"task_spec": task_spec()})

    assert result["scenario_context"][0]["source"].startswith("approved-scenario:")
    assert "33-node feeder" in model.calls[1][1]
    assert "Approved examples from this exact domain" in model.calls[1][1]
