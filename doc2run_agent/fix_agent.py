from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .code_edits import apply_code_patch
from .context import complete_and_record, context_sources, merge_context
from .errors import classify_failure
from .knowledge_tools import KnowledgeSearchTool
from .llm import TextModel
from .parsing import parse_model
from .prompts import (
    FIX_PLAN_SYSTEM,
    REFINEMENT_PLAN_SYSTEM,
    PATCH_REVIEW_SYSTEM,
    PATCH_SYSTEM,
    fix_plan_request,
    patch_request,
    patch_review_request,
)
from .schemas import (
    CodePatch,
    CodeValidation,
    FixPlan,
    OrchestratorState,
    PatchReview,
    RunResult,
    TaskSpec,
)
from .validation import validate_code


def build_fix_agent_graph(
    model: TextModel,
    knowledge_tool: KnowledgeSearchTool,
    domain_knowledge_tool: KnowledgeSearchTool | None = None,
):
    def classify(state: OrchestratorState) -> dict[str, object]:
        if state.get("user_instruction"):
            return {
                "error_info": {
                    "category": "runtime_error",
                    "exception_type": "UserRequestedRefinement",
                    "message": state["user_instruction"],
                    "traceback": "",
                },
                "status": "planning_user_refinement",
            }
        validation = CodeValidation.model_validate(state["code_validation"])
        run_result = (
            RunResult.model_validate(state["run_result"])
            if state.get("run_result") is not None
            else None
        )
        info = classify_failure(run_result, validation)
        return {"error_info": info.model_dump(mode="json"), "status": "classifying_failure"}

    def create_fix_plan(state: OrchestratorState) -> dict[str, object]:
        prompt = fix_plan_request(
            state["task_spec"],
            state.get("implementation_plan", {}),
            state["code"],
            state["error_info"],
            state.get("run_result", {}),
        )
        response, records = complete_and_record(
            model,
            stage="user_refinement_plan" if state.get("user_instruction") else "fix_plan",
            system_prompt=REFINEMENT_PLAN_SYSTEM if state.get("user_instruction") else FIX_PLAN_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
        )
        plan = parse_model(response, FixPlan)
        return {
            "fix_plan": plan.model_dump(mode="json"),
            "retrieval_queries": plan.search_queries,
            "context_records": records,
            "status": "retrieving_fix_context",
        }

    def retrieve(state: OrchestratorState) -> dict[str, object]:
        queries = state.get("retrieval_queries", [])
        return {
            "fix_context": knowledge_tool.search_many(queries) if queries else [],
            "fix_domain_context": (
                domain_knowledge_tool.search_many(queries)
                if queries and domain_knowledge_tool is not None
                else []
            ),
        }

    def propose_patch(state: OrchestratorState) -> dict[str, object]:
        attempt = state.get("fix_attempts", 0) + 1
        api_context = merge_context(
            state.get("retrieved_context", []),
            state.get("additional_context", []),
            state.get("fix_context", []),
        )
        domain_context = merge_context(
            state.get("domain_context", []),
            state.get("additional_domain_context", []),
            state.get("fix_domain_context", []),
        )
        prompt = patch_request(
            state["task_spec"],
            state.get("implementation_plan", {}),
            state["fix_plan"],
            api_context,
            state["code"],
            attempt,
            domain_context=domain_context,
        )
        response, records = complete_and_record(
            model,
            stage=f"fix_patch_{attempt:03d}",
            system_prompt=PATCH_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(merge_context(api_context, domain_context), prompt),
        )
        patch = parse_model(response, CodePatch)
        return {
            "code_patch": patch.model_dump(mode="json"),
            "previous_code": state["code"],
            "fix_attempts": attempt,
            "context_records": records,
        }

    def apply_patch(state: OrchestratorState) -> dict[str, object]:
        patch = CodePatch.model_validate(state["code_patch"])
        code, error = apply_code_patch(
            state["previous_code"], patch, allow_rewrite=state["fix_attempts"] >= 2
        )
        return {"code": code, "patch_error": error, "status": "repaired" if not error else "patch_failed"}

    def review_patch(state: OrchestratorState) -> dict[str, object]:
        api_context = merge_context(
            state.get("retrieved_context", []),
            state.get("additional_context", []),
            state.get("fix_context", []),
        )
        domain_context = merge_context(
            state.get("domain_context", []),
            state.get("additional_domain_context", []),
            state.get("fix_domain_context", []),
        )
        prompt = patch_review_request(
            state["task_spec"],
            state.get("implementation_plan", {}),
            state["fix_plan"],
            state["previous_code"],
            state["code"],
            state.get("patch_error", ""),
            api_context,
            domain_context=domain_context,
        )
        response, records = complete_and_record(
            model,
            stage=f"fix_review_{state['fix_attempts']:03d}",
            system_prompt=PATCH_REVIEW_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(merge_context(api_context, domain_context), prompt),
        )
        review = parse_model(response, PatchReview)
        return {"patch_review": review.model_dump(mode="json"), "context_records": records}

    def validate(state: OrchestratorState) -> dict[str, object]:
        result = validate_code(state["code"], TaskSpec.model_validate(state["task_spec"]))
        errors = list(result.errors)
        if state.get("patch_error"):
            errors.append(state["patch_error"])
        review = PatchReview.model_validate(state["patch_review"])
        if not review.ok:
            errors.extend(review.problems or ["The repair review rejected the code change"])
        value = CodeValidation(ok=not errors, errors=list(dict.fromkeys(errors)), imports=result.imports)
        return {"code_validation": value.model_dump(mode="json")}

    builder = StateGraph(OrchestratorState)
    builder.add_node("classify_error", classify)
    builder.add_node("create_fix_plan", create_fix_plan)
    builder.add_node("search_fix_knowledge", retrieve)
    builder.add_node("propose_code_patch", propose_patch)
    builder.add_node("apply_code_patch", apply_patch)
    builder.add_node("review_code_patch", review_patch)
    builder.add_node("validate_repaired_code", validate)
    builder.add_edge(START, "classify_error")
    builder.add_edge("classify_error", "create_fix_plan")
    builder.add_edge("create_fix_plan", "search_fix_knowledge")
    builder.add_edge("search_fix_knowledge", "propose_code_patch")
    builder.add_edge("propose_code_patch", "apply_code_patch")
    builder.add_edge("apply_code_patch", "review_code_patch")
    builder.add_edge("review_code_patch", "validate_repaired_code")
    builder.add_edge("validate_repaired_code", END)
    return builder.compile()
