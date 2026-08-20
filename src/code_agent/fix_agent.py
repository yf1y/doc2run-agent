from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .errors import classify_failure
from .knowledge_tools import KnowledgeSearchTool
from .llm import TextModel
from .parsing import parse_model
from .prompts import (
    FIX_RETRIEVAL_SYSTEM,
    FIX_SYSTEM,
    fix_request,
    fix_retrieval_request,
)
from .runner import sanitize_code
from .schemas import CodeValidation, OrchestratorState, RetrievalQueryPlan, RunResult, TaskSpec
from .validation import validate_code


def build_fix_agent_graph(model: TextModel, knowledge_tool: KnowledgeSearchTool):
    def classify(state: OrchestratorState) -> dict[str, object]:
        validation = CodeValidation.model_validate(state["code_validation"])
        run_result = (
            RunResult.model_validate(state["run_result"])
            if state.get("run_result") is not None
            else None
        )
        info = classify_failure(run_result, validation)
        return {"error_info": info.model_dump(mode="json"), "status": "classifying_failure"}

    def plan_retrieval(state: OrchestratorState) -> dict[str, object]:
        plan = parse_model(
            model.complete(
                FIX_RETRIEVAL_SYSTEM,
                fix_retrieval_request(state["task_spec"], state["code"], state["error_info"]),
            ),
            RetrievalQueryPlan,
        )
        return {"retrieval_queries": plan.queries, "status": "retrieving_fix_context"}

    def retrieve(state: OrchestratorState) -> dict[str, object]:
        return {"fix_context": knowledge_tool.search_many(state["retrieval_queries"])}

    def repair(state: OrchestratorState) -> dict[str, object]:
        attempt = state.get("fix_attempts", 0) + 1
        combined_context = list(state.get("retrieved_context", [])) + list(state.get("fix_context", []))
        code = sanitize_code(
            model.complete(
                FIX_SYSTEM,
                fix_request(
                    state["task_spec"],
                    combined_context,
                    state["code"],
                    state.get("run_result", {}),
                    state["error_info"],
                    attempt,
                ),
            )
        )
        if not code:
            raise ValueError("Fix Agent returned empty code")
        return {"code": code, "fix_attempts": attempt, "status": "repaired"}

    def validate(state: OrchestratorState) -> dict[str, object]:
        result = validate_code(state["code"], TaskSpec.model_validate(state["task_spec"]))
        return {"code_validation": result.model_dump(mode="json")}

    builder = StateGraph(OrchestratorState)
    builder.add_node("classify_error", classify)
    builder.add_node("plan_fix_retrieval", plan_retrieval)
    builder.add_node("search_fix_knowledge", retrieve)
    builder.add_node("repair_code", repair)
    builder.add_node("validate_repaired_code", validate)
    builder.add_edge(START, "classify_error")
    builder.add_edge("classify_error", "plan_fix_retrieval")
    builder.add_edge("plan_fix_retrieval", "search_fix_knowledge")
    builder.add_edge("search_fix_knowledge", "repair_code")
    builder.add_edge("repair_code", "validate_repaired_code")
    builder.add_edge("validate_repaired_code", END)
    return builder.compile()
