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


def make_orchestrator(tmp_path, responses, max_fix_attempts=2, **options):
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
    model = options.pop("model", None) or FakeModel(responses)
    orchestrator = Doc2RunOrchestrator(
        model,
        KnowledgeSearchTool(LocalKnowledgeBase.from_directory(api)),
        store,
        LocalPythonRunner(timeout_seconds=1),
        max_fix_attempts=max_fix_attempts,
        scene_tool=SceneSearchTool.from_directory(scenes),
        scene_library=SceneLibrary(scenes),
        **options,
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


def test_chat_corrects_format_once_and_keeps_both_replies(tmp_path):
    orchestrator, store, model, _ = make_orchestrator(tmp_path, ["not JSON", complete_chat_response()])
    result = orchestrator.handle_message("retry", "生成 5 节点")
    assert result["status"] == "awaiting_confirmation"
    assert len(model.calls) == 2
    assert "生成 5 节点" in model.calls[1][1]
    assert "JSON schema" in model.calls[1][1]
    directory = store.session_directory("retry")
    manifest = json.loads((directory / "contexts/manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2
    assert "not JSON" in (directory / "contexts" / manifest[0]["file"]).read_text(encoding="utf-8")


def test_chat_exhausts_format_retry_without_losing_input(tmp_path):
    orchestrator, store, model, _ = make_orchestrator(tmp_path, ["invalid first", "invalid second"])
    with pytest.raises(ValueError, match="JSON twice"):
        orchestrator.handle_message("bad", "my original request")
    assert len(model.calls) == 2
    record = store.load_or_create("bad")
    assert record.messages[-1].content == "my original request"
    assert record.phase == "collecting_goal"
    directory = store.session_directory("bad")
    assert len(list((directory / "contexts").glob("*.md"))) == 2
    assert '"status": "failed"' in (directory / "events.jsonl").read_text(encoding="utf-8")


def test_model_exception_is_saved_immediately_and_credentials_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_API_KEY", "secret-not-for-logs")
    orchestrator, store, model, _ = make_orchestrator(tmp_path, [])
    def fail(*_):
        raise ConnectionError("service rejected secret-not-for-logs")
    model.complete = fail
    with pytest.raises(RuntimeError, match="REDACTED"):
        orchestrator.handle_message("network", "task")
    directory = store.session_directory("network")
    saved = "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())
    assert "secret-not-for-logs" not in saved
    assert "ConnectionError" in saved
    manifest = json.loads((directory / "contexts/manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["status"] == "failed"


def test_missing_installed_sdk_stops_without_fix(tmp_path):
    orchestrator, store, model, _ = make_orchestrator(tmp_path, [
        complete_chat_response(), '{"queries": ["json"]}',
        "raise ModuleNotFoundError(\"No module named 'doc2run_missing_sdk_test'\")",
    ])
    orchestrator.handle_message("missing", "task")
    result = orchestrator.confirm("missing")
    assert result["status"] == "failed"
    assert result["fix_attempts"] == 0
    assert "missing dependency/input" in result["assistant_message"]
    assert len(model.calls) == 3
    assert (store.session_directory("missing") / "runs/initial/stderr.txt").exists()


def test_wrong_api_import_still_uses_fix(tmp_path):
    code = "from json import nonexistent_method"
    orchestrator, _, model, _ = make_orchestrator(tmp_path, [
        complete_chat_response(), '{"queries": ["json"]}', code,
        fix_plan_response(), patch_response(code, "print('{}')"), patch_review_response(),
    ])
    orchestrator.handle_message("bad-import", "task")
    result = orchestrator.confirm("bad-import")
    assert result["status"] == "awaiting_review"
    assert result["fix_attempts"] == 1


def test_task_timeout_saves_partial_context_and_cannot_execute_late_reply(tmp_path):
    import threading
    import time
    release = threading.Event()
    returned = threading.Event()
    orchestrator, store, model, _ = make_orchestrator(tmp_path, [complete_chat_response()])
    orchestrator.handle_message("deadline", "task")
    def slow(*_):
        release.wait(3)
        returned.set()
        return '{"queries": ["json"]}'
    model.complete = slow
    orchestrator.task_timeout_seconds = 0.1
    start = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="Task timeout"):
            orchestrator.confirm("deadline")
        assert time.monotonic() - start < 1.5
        directory = store.session_directory("deadline")
        assert not (directory / "workspace/generated.py").exists()
        before = (directory / "events.jsonl").read_text(encoding="utf-8")
        assert "TaskTimeoutError" in before
        release.set()
        assert returned.wait(1)
        assert (directory / "events.jsonl").read_text(encoding="utf-8") == before
    finally:
        release.set()


def test_task_deadline_stops_script_and_preserves_confirmed_plan(tmp_path):
    orchestrator, store, _, _ = make_orchestrator(tmp_path, [
        complete_chat_response(), '{"queries": ["json"]}', "import time\ntime.sleep(3)",
    ])
    orchestrator.handle_message("slow-code", "task")
    orchestrator.task_timeout_seconds = 0.3
    with pytest.raises(RuntimeError, match="Task timeout"):
        orchestrator.confirm("slow-code")
    record = store.load_or_create("slow-code")
    assert record.confirmed_plan == PLAN
    assert record.run_result.timed_out
    assert record.fix_attempts == 0


def test_full_workflow_with_real_http_adapter(tmp_path, model_http_server):
    from doc2run_agent.llm import AgentModelSettings, ModelSettings, create_agent_models
    base, replies, requests = model_http_server
    replies.extend((200, value) for value in [
        "invalid JSON", complete_chat_response(), '{"queries": ["json"]}',
        "raise RuntimeError('broken')", fix_plan_response(),
        patch_response("raise RuntimeError('broken')", "print('{}')"), patch_review_response(),
    ])
    settings = ModelSettings(model="openai/local-test", api_base=base, api_key="local-placeholder",
                             timeout_seconds=10, max_retries=0)
    with create_agent_models(AgentModelSettings(settings, settings, settings)) as models:
        orchestrator, store, _, _ = make_orchestrator(tmp_path, [], model=models)
        assert orchestrator.handle_message("http", "生成 JSON")["status"] == "awaiting_confirmation"
        result = orchestrator.confirm("http")
        assert result["status"] == "awaiting_review"
        assert result["fix_attempts"] == 1
        assert json.loads(result["run_result"]["stdout"]) == {}
        assert orchestrator.approve("http")["status"] == "memory"
    assert len(requests) == 7
    events = [json.loads(line) for line in (store.session_directory("http") / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len([e for e in events if e["stage"] == "model_request" and e["status"] == "started"]) == 7
    assert store.load_or_create("http").confirmed_plan == PLAN


def test_chat_retries_invalid_task_spec_fields(tmp_path):
    invalid = json.loads(complete_chat_response())
    invalid["spec_patch"]["invented_field"] = True
    orchestrator, _, model, _ = make_orchestrator(tmp_path, [json.dumps(invalid), complete_chat_response()])
    assert orchestrator.handle_message("fields", "task")["status"] == "awaiting_confirmation"
    assert len(model.calls) == 2


def test_empty_model_text_gets_one_format_retry(tmp_path):
    from doc2run_agent.llm import LiteLLMModel, ModelSettings
    replies = iter(["", complete_chat_response()])
    model = LiteLLMModel(ModelSettings(model="fake", max_retries=0),
                        completion_fn=lambda **_: {"choices": [{"message": {"content": next(replies)}}]})
    orchestrator, store, _, _ = make_orchestrator(tmp_path, [], model=model)
    assert orchestrator.handle_message("empty", "task")["status"] == "awaiting_confirmation"
    records = json.loads((store.session_directory("empty") / "contexts/manifest.json").read_text(encoding="utf-8"))
    assert len(records) == 2
    assert records[0]["status"] == "failed"
