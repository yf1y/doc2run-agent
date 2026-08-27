"""Top-level workflow that coordinates Chat, Code, Fix, execution, and Memory."""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from ..agents.chat import ChatAgent, missing_sections
from ..agents.code import build_code_agent_graph
from ..agents.fix import build_fix_agent_graph
from ..agents.memory import MemoryAgent
from ..knowledge.scenes import SceneLibrary
from ..knowledge.tools import KnowledgeSearchTool, SceneSearchTool
from ..llm import AgentModels, TextModel, as_agent_models
from ..runtime.errors import classify_failure
from ..runtime.runner import LocalPythonRunner
from ..schemas import CodeValidation, OrchestratorState, RunResult, SessionRecord, TaskSpec
from ..storage.artifacts import ArtifactManager
from ..storage.sessions import FileSessionStore


class Doc2RunOrchestrator:
    """Expose the session-level interface over the internal LangGraph workflow."""

    def __init__(
        self,
        models: TextModel | AgentModels,
        knowledge_tool: KnowledgeSearchTool,
        store: FileSessionStore,
        runner: LocalPythonRunner | None = None,
        *,
        max_fix_attempts: int = 3,
        scene_tool: SceneSearchTool | None = None,
        scene_library: SceneLibrary | None = None,
    ) -> None:
        if max_fix_attempts < 0:
            raise ValueError("max_fix_attempts cannot be negative")
        self.store = store
        self.max_fix_attempts = max_fix_attempts
        selected_scene_library = scene_library or (
            SceneLibrary(scene_tool.source_directory)
            if scene_tool is not None and scene_tool.source_directory is not None
            else None
        )
        self.memory_agent = MemoryAgent(selected_scene_library, store)
        self.graph = build_orchestrator_graph(
            as_agent_models(models),
            knowledge_tool,
            store,
            runner or LocalPythonRunner(),
            max_fix_attempts=max_fix_attempts,
            scene_tool=scene_tool,
        )

    def handle_message(self, session_id: str, user_input: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        if record.phase in {"generating_code", "executing", "repairing"}:
            raise ValueError("Code generation is in progress or was interrupted; enter /confirm to retry it")
        if record.phase in {"awaiting_review", "failed"}:
            return self._invoke(record, event="refine", user_input=user_input)
        if record.phase in {"memory", "approved", "succeeded"}:
            raise ValueError("This version is already approved; enter /reset for a new task")
        return self._invoke(record, event="message", user_input=user_input)

    def confirm(self, session_id: str) -> dict[str, Any]:
        return self._invoke(self.store.load_or_create(session_id), event="confirm")

    def approve(self, session_id: str, note: str = "") -> dict[str, Any]:
        memory = self.memory_agent.approve(self.store.load_or_create(session_id), note)
        record = memory.record
        return {
            "session": record.model_dump(mode="json"),
            "status": "memory",
            "assistant_message": "Memory complete. The approved Scenario Plan was saved as a reusable Scene.",
            "scene_path": str(memory.scene_path),
            "run_result": record.run_result.model_dump(mode="json"),
        }

    def _invoke(
        self,
        record: SessionRecord,
        *,
        event: Literal["message", "confirm", "refine"],
        user_input: str = "",
    ) -> dict[str, Any]:
        return self.graph.invoke(
            {
                "event": event,
                "user_input": user_input,
                "session": record.model_dump(mode="json"),
                "artifact_paths": [],
            },
            config={"recursion_limit": 16 + self.max_fix_attempts * 8},
        )


def build_orchestrator_graph(
    models: TextModel | AgentModels,
    knowledge_tool: KnowledgeSearchTool,
    store: FileSessionStore,
    runner: LocalPythonRunner,
    *,
    max_fix_attempts: int,
    scene_tool: SceneSearchTool | None = None,
):
    models = as_agent_models(models)
    chat_agent = ChatAgent(models.chat, scene_tool)
    code_graph = build_code_agent_graph(models.code, knowledge_tool)
    fix_graph = build_fix_agent_graph(models.fix, knowledge_tool)
    artifacts = ArtifactManager(store)

    def route_event(
        state: OrchestratorState,
    ) -> Literal["chat_agent", "confirm_task", "begin_refinement"]:
        if state["event"] == "message":
            return "chat_agent"
        return "confirm_task" if state["event"] == "confirm" else "begin_refinement"

    def begin_refinement(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        if not record.confirmed_spec or not record.confirmed_plan or not record.generated_code:
            raise ValueError("There is no generated version to refine")
        base_attempt = record.fix_attempts
        record.phase = "repairing"
        record.status = "repairing"
        value = {
            "session": record.model_dump(mode="json"),
            "task_spec": record.confirmed_spec.model_dump(mode="json"),
            "scenario_plan": record.confirmed_plan,
            "retrieved_context": list(record.retrieved_context),
            "code": record.generated_code,
            "code_validation": (
                record.code_validation.model_dump(mode="json")
                if record.code_validation
                else {"ok": True, "errors": [], "imports": []}
            ),
            "run_result": record.run_result.model_dump(mode="json") if record.run_result else {},
            "run_history": list(record.run_history),
            "fix_attempts": base_attempt,
            "fix_attempt_limit": base_attempt + max_fix_attempts,
            "user_instruction": state["user_input"],
            "context_records": [],
            "status": "repairing",
        }
        store.save(record)
        return value

    def collect_chat(state: OrchestratorState) -> dict[str, Any]:
        turn = chat_agent.process_turn(
            SessionRecord.model_validate(state["session"]), state["user_input"]
        )
        record = turn.record
        value: dict[str, Any] = {
            "session": record.model_dump(mode="json"),
            "assistant_message": record.messages[-1].content,
            "status": record.status,
        }
        value["context_records"] = turn.context_records
        return value

    def persist_message(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        store.save(record)
        paths = [
            artifacts.save_decisions(record.session_id, record.decisions),
            artifacts.save_selected_scene(record.session_id, record.selected_scene),
            artifacts.save_scenario_plan(record.session_id, record.draft_plan),
        ]
        paths.extend(artifacts.save_context_records(record.session_id, state.get("context_records", [])))
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(state, paths),
        }

    def confirm_task(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        if record.phase == "awaiting_confirmation":
            missing = missing_sections(
                record.draft_spec, record.confirmed_sections, record.draft_plan
            )
            if missing:
                raise ValueError(f"Task specification or Scenario Plan is incomplete: {missing}")
            snapshot = store.snapshot_confirmed_spec(record)
        elif record.phase in {"generating_code", "executing", "repairing"} and record.confirmed_spec:
            snapshot = record.confirmed_spec
        else:
            raise ValueError("Task specification is not awaiting confirmation")
        if not record.confirmed_plan:
            raise ValueError("Scenario Plan is not confirmed")
        record.phase = "generating_code"
        record.status = "generating_code"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "task_spec": snapshot.model_dump(mode="json"),
            "scenario_plan": record.confirmed_plan,
            "decisions": list(record.decisions),
            "fix_attempts": 0,
            "fix_attempt_limit": max_fix_attempts,
            "run_history": [],
            "context_records": [],
            "status": "generating_code",
        }

    def persist_code_generation(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        paths: list[Any] = [
            artifacts.save_retrieval(
                record.session_id,
                stage="api_knowledge",
                round_index=1,
                queries=state["retrieval_queries"],
                context=state["retrieved_context"],
            ),
            artifacts.save_api_context(record.session_id, state["retrieved_context"]),
        ]
        if state.get("code") and state.get("code_validation"):
            paths.extend(
                artifacts.save_generation(
                    record.session_id,
                    attempt=0,
                    code=state["code"],
                    validation=state["code_validation"],
                )
            )
        paths.extend(artifacts.save_context_records(record.session_id, state.get("context_records", [])))
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(state, paths),
        }

    def persist_fix_generation(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        record.phase = "repairing"
        attempt = state["fix_attempts"]
        paths: list[Any] = [
            artifacts.save_retrieval(
                record.session_id,
                stage="fix_api_knowledge",
                round_index=attempt,
                queries=state["retrieval_queries"],
                context=state.get("fix_context", []),
            )
        ]
        paths.extend(
            artifacts.save_generation(
                record.session_id,
                attempt=attempt,
                code=state["code"],
                validation=state["code_validation"],
            )
        )
        paths.extend(
            artifacts.save_fix_details(
                record.session_id,
                attempt=attempt,
                fix_plan=state["fix_plan"],
                code_patch=state["code_patch"],
                patch_review=state["patch_review"],
                patch_error=state.get("patch_error", ""),
            )
        )
        paths.extend(artifacts.save_context_records(record.session_id, state.get("context_records", [])))
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(state, paths),
            "user_instruction": "",
        }

    def route_after_fix_agent(state: OrchestratorState) -> Literal[
        "persist_fix_generation", "complete_refinement_conflict"
    ]:
        if state.get("status") == "refinement_conflict":
            return "complete_refinement_conflict"
        return "persist_fix_generation"

    def complete_refinement_conflict(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        record.phase = "awaiting_review"
        record.status = "awaiting_review"
        paths = [
            artifacts.save_refinement_conflict(
                record.session_id,
                instruction=state.get("user_instruction", ""),
                fix_plan=state["fix_plan"],
            )
        ]
        paths.extend(
            artifacts.save_context_records(
                record.session_id, state.get("context_records", [])
            )
        )
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "awaiting_review",
            "assistant_message": (
                "This request changes the confirmed TaskSpec or Scenario Plan, so the working "
                "version was left unchanged. Enter /reset to define and confirm a new contract, "
                "or request an implementation-only refinement."
            ),
            "user_instruction": "",
            "artifact_paths": _append_paths(state, paths),
        }

    def route_after_validation(state: OrchestratorState) -> Literal["execute", "fix_agent", "failed"]:
        if state["code_validation"]["ok"]:
            return "execute"
        return "failed" if repair_budget_exhausted(state) else "fix_agent"

    def execute(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        record.phase = "executing"
        record.status = "executing"
        store.save(record)
        result = runner.run(state["code"], artifacts.workspace(record.session_id))
        result_dict = result.model_dump(mode="json")
        history = list(state.get("run_history", []))
        history.append(
            {
                "attempt": state.get("fix_attempts", 0),
                "validation": state["code_validation"],
                "run_result": result_dict,
            }
        )
        paths = artifacts.save_execution(
            record.session_id, attempt=state.get("fix_attempts", 0), run_result=result_dict
        )
        record.run_result = result
        record.run_history = history
        record.generated_code = state["code"]
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "run_result": result_dict,
            "run_history": history,
            "status": "succeeded" if result.ok else "execution_failed",
            "artifact_paths": _append_paths(state, paths),
        }

    def route_after_execute(state: OrchestratorState) -> Literal["succeeded", "fix_agent", "failed"]:
        if state["run_result"]["ok"]:
            return "succeeded"
        return "failed" if repair_budget_exhausted(state) else "fix_agent"

    def repair_budget_exhausted(state: OrchestratorState) -> bool:
        return state.get("fix_attempts", 0) >= state.get("fix_attempt_limit", max_fix_attempts)

    def complete_success(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        record.phase = "awaiting_review"
        record.status = "awaiting_review"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "awaiting_review",
            "assistant_message": "Code ran successfully. Describe a change to refine it, or enter /approve to approve this version and save its Scenario Plan as a Scene.",
        }

    def complete_failure(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        validation = CodeValidation.model_validate(state["code_validation"])
        run_result = RunResult.model_validate(state["run_result"]) if state.get("run_result") else None
        info = classify_failure(run_result, validation)
        record.phase = "failed"
        record.status = "failed"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "failed",
            "error_info": info.model_dump(mode="json"),
            "assistant_message": "The repair limit was reached. Review the saved run artifacts.",
        }

    builder = StateGraph(OrchestratorState)
    builder.add_node("chat_agent", collect_chat)
    builder.add_node("persist_message", persist_message)
    builder.add_node("confirm_task", confirm_task)
    builder.add_node("begin_refinement", begin_refinement)
    builder.add_node("code_agent", code_graph)
    builder.add_node("persist_code_generation", persist_code_generation)
    builder.add_node("execute", execute)
    builder.add_node("fix_agent", fix_graph)
    builder.add_node("persist_fix_generation", persist_fix_generation)
    builder.add_node("complete_refinement_conflict", complete_refinement_conflict)
    builder.add_node("complete_success", complete_success)
    builder.add_node("complete_failure", complete_failure)
    builder.add_conditional_edges(
        START,
        route_event,
        {
            "chat_agent": "chat_agent",
            "confirm_task": "confirm_task",
            "begin_refinement": "begin_refinement",
        },
    )
    builder.add_edge("chat_agent", "persist_message")
    builder.add_edge("persist_message", END)
    builder.add_edge("confirm_task", "code_agent")
    builder.add_edge("begin_refinement", "fix_agent")
    builder.add_edge("code_agent", "persist_code_generation")
    builder.add_conditional_edges(
        "persist_code_generation",
        route_after_validation,
        {"execute": "execute", "fix_agent": "fix_agent", "failed": "complete_failure"},
    )
    builder.add_conditional_edges(
        "execute",
        route_after_execute,
        {"succeeded": "complete_success", "fix_agent": "fix_agent", "failed": "complete_failure"},
    )
    builder.add_conditional_edges(
        "fix_agent",
        route_after_fix_agent,
        {
            "persist_fix_generation": "persist_fix_generation",
            "complete_refinement_conflict": "complete_refinement_conflict",
        },
    )
    builder.add_conditional_edges(
        "persist_fix_generation",
        route_after_validation,
        {"execute": "execute", "fix_agent": "fix_agent", "failed": "complete_failure"},
    )
    builder.add_edge("complete_success", END)
    builder.add_edge("complete_failure", END)
    builder.add_edge("complete_refinement_conflict", END)
    return builder.compile()


def _sync_record(record: SessionRecord, state: OrchestratorState) -> SessionRecord:
    if state.get("task_spec"):
        record.confirmed_spec = TaskSpec.model_validate(state["task_spec"])
    if state.get("scenario_plan") and not record.confirmed_plan:
        record.confirmed_plan = str(state["scenario_plan"])
    record.retrieval_queries = list(state.get("retrieval_queries", []))
    record.retrieved_context = list(state.get("retrieved_context", record.retrieved_context))
    record.generated_code = state.get("code", record.generated_code)
    if state.get("code_validation"):
        record.code_validation = CodeValidation.model_validate(state["code_validation"])
    if state.get("run_result"):
        record.run_result = RunResult.model_validate(state["run_result"])
    record.run_history = list(state.get("run_history", record.run_history))
    record.fix_attempts = state.get("fix_attempts", record.fix_attempts)
    record.status = state.get("status", record.status)
    return record


def _append_paths(state: OrchestratorState, paths: list[Any]) -> list[str]:
    return list(state.get("artifact_paths", [])) + [str(path) for path in paths]
