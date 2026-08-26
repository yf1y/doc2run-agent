from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from .artifacts import ArtifactManager
from .generation_agent import build_generation_agent_graph
from .errors import classify_failure
from .fix_agent import build_fix_agent_graph
from .knowledge_tools import KnowledgeSearchTool
from .llm import AgentModels, TextModel, as_agent_models
from .memory_agent import MemoryAgent
from .memory_store import ScenarioMemoryStore
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
        scenario_memory: ScenarioMemoryStore | None = None,
        domain: str = "",
        domain_knowledge_tool: KnowledgeSearchTool | None = None,
    ) -> None:
        if max_fix_attempts < 0:
            raise ValueError("max_fix_attempts cannot be negative")
        self.store = store
        self.max_fix_attempts = max_fix_attempts
        self.domain = domain
        self.scenario_memory = scenario_memory
        selected_models = as_agent_models(models)
        self.memory_agent = (
            MemoryAgent(selected_models.code, scenario_memory) if scenario_memory is not None else None
        )
        self.graph = build_orchestrator_graph(
            selected_models,
            knowledge_tool,
            store,
            runner or LocalPythonRunner(),
            max_fix_attempts=max_fix_attempts,
            scenario_memory=scenario_memory,
            domain=domain,
            domain_knowledge_tool=domain_knowledge_tool,
        )

    def handle_message(self, session_id: str, user_input: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        if record.phase in {"generating_code", "executing", "repairing"}:
            raise ValueError("Generation is in progress or was interrupted; enter /confirm to retry it")
        if record.phase in {"awaiting_review", "failed"}:
            self._require_domain_match(record)
            return self._invoke(record, event="refine", user_input=user_input)
        if record.phase in {"memory_candidate_ready", "approved", "succeeded"}:
            raise ValueError("This version is already approved; enter /reset for a new task")
        return self._invoke(record, event="message", user_input=user_input)

    def confirm(self, session_id: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        return self._invoke(record, event="confirm")

    def approve(self, session_id: str, note: str = "") -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        self._require_domain_match(record)
        if record.phase != "awaiting_review" or not record.run_result or not record.run_result.ok:
            raise ValueError("Only a successfully executed version awaiting review can be approved")
        record.approval_note = note.strip()
        if not self.domain:
            record.phase = "approved"
            record.status = "approved"
            self.store.save(record)
            return {
                "session": record.model_dump(mode="json"),
                "status": "approved",
                "assistant_message": "Code approved. No domain was selected, so no scenario memory was created.",
                "run_result": record.run_result.model_dump(mode="json"),
            }
        if self.memory_agent is None:
            raise ValueError("Scenario memory is not configured")
        result = self.memory_agent.create_candidate(
            session_id=session_id,
            domain=self.domain,
            task_spec=record.confirmed_spec.model_dump(mode="json") if record.confirmed_spec else {},
            implementation_plan=record.implementation_plan or {},
            code=record.generated_code,
            run_result=record.run_result.model_dump(mode="json"),
            approval_note=record.approval_note,
        )
        record.active_domain = self.domain
        record.memory_candidate_id = result["candidate_id"]
        record.memory_candidate = result["candidate"]
        record.memory_validation_errors = result["validation_errors"]
        record.memory_review = result["review"]
        record.phase = "memory_candidate_ready"
        record.status = "memory_candidate_ready"
        self.store.save(record)
        ArtifactManager(self.store).save_context_records(session_id, result["context_records"])
        review_ok = bool(result["review"].get("ok")) and not result["validation_errors"]
        message = (
            "Code approved. The isolated memory review passed; enter /remember to add this scenario."
            if review_ok
            else "Code approved, but the scenario candidate failed review. Inspect it or enter /reject-memory."
        )
        return {
            "session": record.model_dump(mode="json"),
            "status": "memory_candidate_ready",
            "assistant_message": message,
            "memory_candidate_path": result["path"],
            "memory_review": result["review"],
            "memory_validation_errors": result["validation_errors"],
            "run_result": record.run_result.model_dump(mode="json"),
        }

    def remember(self, session_id: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        self._require_domain_match(record)
        if record.phase != "memory_candidate_ready" or not record.memory_candidate_id:
            raise ValueError("There is no pending scenario candidate")
        if self.scenario_memory is None or not record.active_domain:
            raise ValueError("Scenario memory is not configured")
        path = self.scenario_memory.approve(record.active_domain, record.memory_candidate_id)
        record.phase = "approved"
        record.status = "approved"
        self.store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "approved",
            "assistant_message": "Scenario memory approved and added to this domain only.",
            "memory_path": str(path),
        }

    def reject_memory(self, session_id: str) -> dict[str, Any]:
        record = self.store.load_or_create(session_id)
        self._require_domain_match(record)
        if record.phase != "memory_candidate_ready" or not record.memory_candidate_id:
            raise ValueError("There is no pending scenario candidate")
        if self.scenario_memory is None or not record.active_domain:
            raise ValueError("Scenario memory is not configured")
        path = self.scenario_memory.reject(record.active_domain, record.memory_candidate_id)
        record.phase = "approved"
        record.status = "approved"
        self.store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "approved",
            "assistant_message": "Scenario candidate rejected and archived; the code remains approved.",
            "memory_path": str(path),
        }

    def _require_domain_match(self, record: SessionRecord) -> None:
        if record.confirmed_spec is not None and record.active_domain != self.domain:
            selected = record.active_domain or "(disabled)"
            current = self.domain or "(disabled)"
            raise ValueError(
                f"Session domain is {selected}, but this run selected {current}; reopen it with the original --domain"
            )

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
    scenario_memory: ScenarioMemoryStore | None = None,
    domain: str = "",
    domain_knowledge_tool: KnowledgeSearchTool | None = None,
):
    models = as_agent_models(models)
    requirements_agent = RequirementsAgent(models.requirements)
    generation_graph = build_generation_agent_graph(
        models.code,
        knowledge_tool,
        scenario_memory=scenario_memory,
        domain=domain,
        domain_knowledge_tool=domain_knowledge_tool,
    )
    fix_graph = build_fix_agent_graph(
        models.fix, knowledge_tool, domain_knowledge_tool=domain_knowledge_tool
    )
    artifacts = ArtifactManager(store)

    def route_event(
        state: OrchestratorState,
    ) -> Literal["requirements_agent", "confirm_task", "begin_refinement"]:
        if state["event"] == "message":
            return "requirements_agent"
        return "confirm_task" if state["event"] == "confirm" else "begin_refinement"

    def begin_refinement(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        if not record.confirmed_spec or not record.generated_code:
            raise ValueError("There is no generated version to refine")
        base_attempt = record.fix_attempts
        record.phase = "repairing"
        record.status = "repairing"
        self_contained = {
            "session": record.model_dump(mode="json"),
            "task_spec": record.confirmed_spec.model_dump(mode="json"),
            "implementation_plan": record.implementation_plan or {},
            "retrieved_context": list(record.retrieved_context),
            "domain_context": list(record.domain_context),
            "scenario_context": list(record.scenario_context),
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
        return self_contained

    def collect_requirements(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        record = requirements_agent.process(record, state["user_input"])
        value = {
            "session": record.model_dump(mode="json"),
            "assistant_message": record.messages[-1].content,
            "status": record.status,
        }
        if requirements_agent.last_context_record is not None:
            value["context_records"] = [requirements_agent.last_context_record]
        return value

    def persist_message(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        store.save(record)
        paths = [artifacts.save_decisions(record.session_id, record.decisions)]
        paths.extend(artifacts.save_context_records(record.session_id, state.get("context_records", [])))
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(state, paths),
        }

    def confirm_task(state: OrchestratorState) -> dict[str, Any]:
        record = SessionRecord.model_validate(state["session"])
        if record.phase == "awaiting_confirmation":
            missing = missing_sections(record.draft_spec, record.confirmed_sections)
            if missing:
                raise ValueError(f"Task specification is incomplete: {missing}")
            snapshot = store.snapshot_confirmed_spec(record)
            record.active_domain = domain
        elif record.phase in {"generating_code", "executing", "repairing"} and record.confirmed_spec:
            if record.active_domain != domain:
                raise ValueError("The selected domain does not match this confirmed session")
            snapshot = record.confirmed_spec
        else:
            raise ValueError("Task specification is not awaiting confirmation")
        record.phase = "generating_code"
        record.status = "generating_code"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "task_spec": snapshot.model_dump(mode="json"),
            "decisions": list(record.decisions),
            "fix_attempts": 0,
            "fix_attempt_limit": max_fix_attempts,
            "run_history": [],
            "context_records": [],
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
        extra_paths: list[Any] = []
        if domain:
            extra_paths.append(
                artifacts.save_retrieval(
                    record.session_id,
                    stage="domain_knowledge",
                    round_index=1,
                    queries=state["retrieval_queries"],
                    context=state.get("domain_context", []),
                )
            )
        if state.get("additional_retrieval_queries"):
            extra_paths.append(
                artifacts.save_retrieval(
                    record.session_id,
                    stage="generation_agent_followup",
                    round_index=2,
                    queries=state["additional_retrieval_queries"],
                    context=state.get("additional_context", []),
                )
            )
            if domain:
                extra_paths.append(
                    artifacts.save_retrieval(
                        record.session_id,
                        stage="domain_knowledge_followup",
                        round_index=2,
                        queries=state["additional_retrieval_queries"],
                        context=state.get("additional_domain_context", []),
                    )
                )
        planning_paths = artifacts.save_planning(
            record.session_id,
            initial_context=state["retrieved_context"],
            additional_context=state.get("additional_context", []),
            domain_context=state.get("domain_context", []),
            additional_domain_context=state.get("additional_domain_context", []),
            scenario_context=state.get("scenario_context", []),
            initial_implementation_plan=state.get(
                "initial_implementation_plan", state["implementation_plan"]
            ),
            implementation_plan=state["implementation_plan"],
            initial_plan_review=state.get("initial_plan_review", state["plan_review"]),
            plan_review=state["plan_review"],
        )
        generation_paths: list[Any] = []
        if state.get("code") and state.get("code_validation"):
            generation_paths = artifacts.save_generation(
                record.session_id,
                attempt=0,
                code=state["code"],
                validation=state["code_validation"],
            )
        context_paths = artifacts.save_context_records(
            record.session_id, state.get("context_records", [])
        )
        store.save(record)
        if state.get("status") == "plan_rejected":
            record.phase = "generating_code"
            record.status = "plan_rejected"
            store.save(record)
            problems = state.get("plan_review", {}).get("problems", [])
            searches = state.get("plan_review", {}).get("search_queries", [])
            details = problems or [
                f"More documentation requested: {query}" for query in searches
            ]
            return {
                "session": record.model_dump(mode="json"),
                "status": "plan_rejected",
                "assistant_message": (
                    "The final implementation plan was not ready, so no code was generated. "
                    f"Review details: {details}. Enter /confirm to retry from the confirmed TaskSpec."
                ),
                "artifact_paths": _append_paths(
                    state,
                    [retrieval_path, *extra_paths, *planning_paths, *context_paths],
                ),
            }
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(
                state,
                [retrieval_path, *extra_paths, *planning_paths, *generation_paths, *context_paths],
            ),
        }

    def persist_fix_generation(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        record.phase = "repairing"
        attempt = state["fix_attempts"]
        retrieval_paths = [
            artifacts.save_retrieval(
                record.session_id,
                stage="fix_agent",
                round_index=attempt,
                queries=state["retrieval_queries"],
                context=state.get("fix_context", []),
            )
        ]
        if record.active_domain:
            retrieval_paths.append(
                artifacts.save_retrieval(
                    record.session_id,
                    stage="fix_agent_domain_knowledge",
                    round_index=attempt,
                    queries=state["retrieval_queries"],
                    context=state.get("fix_domain_context", []),
                )
            )
        generation_paths = artifacts.save_generation(
            record.session_id,
            attempt=attempt,
            code=state["code"],
            validation=state["code_validation"],
        )
        fix_paths = artifacts.save_fix_details(
            record.session_id,
            attempt=attempt,
            fix_plan=state["fix_plan"],
            code_patch=state["code_patch"],
            patch_review=state["patch_review"],
            patch_error=state.get("patch_error", ""),
        )
        context_paths = artifacts.save_context_records(
            record.session_id, state.get("context_records", [])
        )
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "artifact_paths": _append_paths(
                state, [*retrieval_paths, *generation_paths, *fix_paths, *context_paths]
            ),
            # The first patch has now consumed the user's refinement request.
            # Any later loop must diagnose the new validation/runtime result.
            "user_instruction": "",
        }

    def route_after_validation(state: OrchestratorState) -> Literal["execute", "fix_agent", "failed"]:
        if state["code_validation"]["ok"]:
            return "execute"
        if state.get("fix_attempts", 0) >= state.get("fix_attempt_limit", max_fix_attempts):
            return "failed"
        return "fix_agent"

    def route_after_generation_persist(
        state: OrchestratorState,
    ) -> Literal["execute", "fix_agent", "failed", "blocked"]:
        if state.get("status") == "plan_rejected":
            return "blocked"
        return route_after_validation(state)

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
                "code_artifact": str(
                    artifacts._run_directory(state.get("fix_attempts", 0)) / "generated.py"
                ),
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
        if state.get("fix_attempts", 0) >= state.get("fix_attempt_limit", max_fix_attempts):
            return "failed"
        return "fix_agent"

    def complete_success(state: OrchestratorState) -> dict[str, Any]:
        record = _sync_record(SessionRecord.model_validate(state["session"]), state)
        record.phase = "awaiting_review"
        record.status = "awaiting_review"
        store.save(record)
        return {
            "session": record.model_dump(mode="json"),
            "status": "awaiting_review",
            "assistant_message": (
                "Code ran successfully. Describe a change to refine it, or enter /approve to approve this version."
            ),
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
    builder.add_node("begin_refinement", begin_refinement)
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
        {
            "requirements_agent": "requirements_agent",
            "confirm_task": "confirm_task",
            "begin_refinement": "begin_refinement",
        },
    )
    builder.add_edge("requirements_agent", "persist_message")
    builder.add_edge("persist_message", END)
    builder.add_edge("confirm_task", "generation_agent")
    builder.add_edge("begin_refinement", "fix_agent")
    builder.add_edge("generation_agent", "persist_code_generation")
    builder.add_conditional_edges(
        "persist_code_generation",
        route_after_generation_persist,
        {
            "execute": "execute",
            "fix_agent": "fix_agent",
            "failed": "complete_failure",
            "blocked": END,
        },
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
    record.domain_context = list(state.get("domain_context", record.domain_context))
    record.scenario_context = list(state.get("scenario_context", record.scenario_context))
    if state.get("additional_context"):
        known = {str(item.get("source", "")) for item in record.retrieved_context}
        record.retrieved_context.extend(
            item
            for item in state["additional_context"]
            if str(item.get("source", "")) not in known
        )
    if state.get("additional_domain_context"):
        known = {str(item.get("source", "")) for item in record.domain_context}
        record.domain_context.extend(
            item
            for item in state["additional_domain_context"]
            if str(item.get("source", "")) not in known
        )
    if state.get("implementation_plan"):
        record.implementation_plan = dict(state["implementation_plan"])
    if state.get("plan_review"):
        record.plan_review = dict(state["plan_review"])
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
