from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from .artifacts import ArtifactManager
from .generation_agent import build_generation_agent_graph
from .errors import classify_failure
from .fix_agent import build_fix_agent_graph
from .knowledge_tools import KnowledgeSearchTool
from .llm import AgentModels, TextModel, as_agent_models
from .requirements_agent import RequirementsAgent, missing_sections
from .runner import LocalPythonRunner
from .schemas import CodeValidation, OrchestratorState, RunResult, SessionRecord, TaskSpec
from .session_store import FileSessionStore


class Doc2RunOrchestrator:
    def __init__(
        self,
        models: TextModel | AgentModels,
        knowledge_tool: KnowledgeSearchTool,
        store: FileSessionStore,
        runner: LocalPythonRunner | None = None,
        *,
        max_fix_attempts: int = 3,
    ) -> None:
        if max_fix_attempts < 0:
            raise ValueError("max_fix_attempts cannot be negative")
        self.store = store
        self.max_fix_attempts = max_fix_attempts
        self.graph = build_orchestrator_graph(
            as_agent_models(models),
            knowledge_tool,
            store,
            runner or LocalPythonRunner(),
            max_fix_attempts=max_fix_attempts,
        )

    def handle_message(self, session_id: str, user_input: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        if record.phase in {"generating_code", "executing", "repairing"}:
            raise ValueError("Generation is in progress or was interrupted; enter /confirm to retry it")
        if record.phase in {"succeeded", "failed"}:
            raise ValueError("This session is complete; enter /reset or choose a new session ID")
        return self._invoke(record, event="message", user_input=user_input)

    def confirm(self, session_id: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        return self._invoke(record, event="confirm")

    def _invoke(
        self,
        record: SessionRecord,
        *,
        event: Literal["message", "confirm"],
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
):
    models = as_agent_models(models)
    requirements_agent = RequirementsAgent(models.requirements)
    generation_graph = build_generation_agent_graph(models.code, knowledge_tool)
    fix_graph = build_fix_agent_graph(models.fix, knowledge_tool)
    artifacts = ArtifactManager(store)

    def route_event(state: OrchestratorState) -> Literal["requirements_agent", "confirm_task"]:
        return "requirements_agent" if state["event"] == "message" else "confirm_task"

    def collect_requirements(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        record = requirements_agent.process(record, state["user_input"])
        return {
            "session": record.model_dump(mode="json"),
            "assistant_message": record.messages[-1].content,
            "status": record.status,
        }

    def persist_message(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        store.save(record)
        return {"session": record.model_dump(mode="json")}

    def confirm_task(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        if record.phase == "awaiting_confirmation":
            missing = missing_sections(record.draft_spec, record.confirmed_sections)
            if missing:
                raise ValueError(f"Task specification is incomplete: {missing}")
            snapshot = store.snapshot_confirmed_spec(record)
        elif record.phase in {"generating_code", "executing", "repairing"} and record.confirmed_spec:
            snapshot = record.confirmed_spec
        else:
            raise ValueError("Task specification is not awaiting confirmation")
        record.phase = "generating_code"
        record.status = "generating_code"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "task_spec": snapshot.model_dump(mode="json"),
            "fix_attempts": 0,
            "run_history": [],
            "status": "generating_code",
        }

    def persist_code_generation(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        retrieval_path = artifacts.save_retrieval(
            record.session_id,
            stage="generation_agent",
            round_index=1,
            queries=state["retrieval_queries"],
            context=state["retrieved_context"],
        )
        generation_paths = artifacts.save_generation(
            record.session_id,
            attempt=0,
            code=state["code"],
            validation=state["code_validation"],
        )
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(state, [retrieval_path, *generation_paths]),
        }

    def persist_fix_generation(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        record.phase = "repairing"
        attempt = state["fix_attempts"]
        retrieval_path = artifacts.save_retrieval(
            record.session_id,
            stage="fix_agent",
            round_index=attempt,
            queries=state["retrieval_queries"],
            context=state.get("fix_context", []),
        )
        generation_paths = artifacts.save_generation(
            record.session_id,
            attempt=attempt,
            code=state["code"],
            validation=state["code_validation"],
        )
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(state, [retrieval_path, *generation_paths]),
        }

    def route_after_validation(state: OrchestratorState) -> Literal["execute", "fix_agent", "failed"]:
        if state["code_validation"]["ok"]:
            return "execute"
        if state.get("fix_attempts", 0) >= max_fix_attempts:
            return "failed"
        return "fix_agent"

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
                "code": state["code"],
                "validation": state["code_validation"],
                "run_result": result_dict,
            }
        )
        execution_paths = artifacts.save_execution(
            record.session_id,
            attempt=state.get("fix_attempts", 0),
            run_result=result_dict,
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
            "artifact_paths": _append_paths(state, execution_paths),
        }

    def route_after_execute(state: OrchestratorState) -> Literal["succeeded", "fix_agent", "failed"]:
        if state["run_result"]["ok"]:
            return "succeeded"
        if state.get("fix_attempts", 0) >= max_fix_attempts:
            return "failed"
        return "fix_agent"

    def complete_success(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        record.phase = "succeeded"
        record.status = "succeeded"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "succeeded",
            "assistant_message": "Code generation and execution completed successfully.",
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
    builder.add_node("requirements_agent", collect_requirements)
    builder.add_node("persist_message", persist_message)
    builder.add_node("confirm_task", confirm_task)
    builder.add_node("generation_agent", generation_graph)
    builder.add_node("persist_code_generation", persist_code_generation)
    builder.add_node("execute", execute)
    builder.add_node("fix_agent", fix_graph)
    builder.add_node("persist_fix_generation", persist_fix_generation)
    builder.add_node("complete_success", complete_success)
    builder.add_node("complete_failure", complete_failure)
    builder.add_conditional_edges(
        START,
        route_event,
        {"requirements_agent": "requirements_agent", "confirm_task": "confirm_task"},
    )
    builder.add_edge("requirements_agent", "persist_message")
    builder.add_edge("persist_message", END)
    builder.add_edge("confirm_task", "generation_agent")
    builder.add_edge("generation_agent", "persist_code_generation")
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
    builder.add_edge("fix_agent", "persist_fix_generation")
    builder.add_conditional_edges(
        "persist_fix_generation",
        route_after_validation,
        {"execute": "execute", "fix_agent": "fix_agent", "failed": "complete_failure"},
    )
    builder.add_edge("complete_success", END)
    builder.add_edge("complete_failure", END)
    return builder.compile()


def _sync_record(record: SessionRecord, state: OrchestratorState) -> SessionRecord:
    if state.get("task_spec"):
        record.confirmed_spec = TaskSpec.model_validate(state["task_spec"])
    record.retrieval_queries = list(state.get("retrieval_queries", []))
    record.retrieved_context = list(state.get("retrieved_context", []))
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
