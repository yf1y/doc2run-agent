from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .context import complete_and_record, context_sources, merge_context
from .knowledge_tools import KnowledgeSearchTool
from .llm import TextModel
from .memory_store import ScenarioMemoryStore
from .parsing import parse_model
from .prompts import (
    CODE_SYSTEM,
    IMPLEMENTATION_PLAN_SYSTEM,
    PLAN_REVIEW_SYSTEM,
    PLAN_REVISION_SYSTEM,
    RETRIEVAL_PLAN_SYSTEM,
    code_request,
    implementation_plan_request,
    plan_review_request,
    plan_revision_request,
    retrieval_plan_request,
)
from .runner import sanitize_code
from .schemas import ImplementationPlan, OrchestratorState, PlanReview, RetrievalQueryPlan, TaskSpec
from .validation import validate_code


def build_generation_agent_graph(
    model: TextModel,
    knowledge_tool: KnowledgeSearchTool,
    scenario_memory: ScenarioMemoryStore | None = None,
    domain: str = "",
):
    def plan_retrieval(state: OrchestratorState) -> dict[str, object]:
        prompt = retrieval_plan_request(state["task_spec"], state.get("decisions", []))
        response, records = complete_and_record(
            model,
            stage="initial_retrieval_plan",
            system_prompt=RETRIEVAL_PLAN_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
        )
        plan = parse_model(response, RetrievalQueryPlan)
        return {
            "retrieval_queries": plan.queries,
            "context_records": records,
            "status": "retrieving_code_context",
        }

    def retrieve(state: OrchestratorState) -> dict[str, object]:
        context = knowledge_tool.search_many(state["retrieval_queries"])
        scenario_context: list[dict[str, Any]] = []
        if scenario_memory is not None and domain:
            spec = TaskSpec.model_validate(state["task_spec"])
            scenario_query = " ".join([spec.objective, *spec.steps, *spec.acceptance_criteria])
            scenario_context = scenario_memory.search(domain, scenario_query, top_k=2)
        return {"retrieved_context": context, "scenario_context": scenario_context}

    def create_plan(state: OrchestratorState) -> dict[str, object]:
        prompt = implementation_plan_request(
            state["task_spec"], state["retrieved_context"], state.get("scenario_context", [])
        )
        response, records = complete_and_record(
            model,
            stage="implementation_plan",
            system_prompt=IMPLEMENTATION_PLAN_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(
                merge_context(state["retrieved_context"], state.get("scenario_context", [])),
                prompt,
            ),
        )
        plan = parse_model(response, ImplementationPlan)
        value = plan.model_dump(mode="json")
        return {
            "initial_implementation_plan": value,
            "implementation_plan": value,
            "context_records": records,
        }

    def review_plan(state: OrchestratorState) -> dict[str, object]:
        prompt = plan_review_request(
            state["task_spec"],
            state["implementation_plan"],
            state["retrieved_context"],
            state.get("scenario_context", []),
        )
        response, records = complete_and_record(
            model,
            stage="implementation_plan_review",
            system_prompt=PLAN_REVIEW_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(state["retrieved_context"], prompt),
        )
        review = parse_model(response, PlanReview)
        value = review.model_dump(mode="json")
        return {
            "initial_plan_review": value,
            "plan_review": value,
            "additional_retrieval_queries": review.search_queries,
            "context_records": records,
        }

    def retrieve_missing(state: OrchestratorState) -> dict[str, object]:
        queries = state.get("additional_retrieval_queries", [])
        return {"additional_context": knowledge_tool.search_many(queries) if queries else []}

    def route_after_review(state: OrchestratorState) -> str:
        review = PlanReview.model_validate(state["plan_review"])
        return "generate_code" if review.ok and not review.search_queries else "search_missing_knowledge"

    def revise_plan(state: OrchestratorState) -> dict[str, object]:
        prompt = plan_revision_request(
            state["task_spec"],
            state["implementation_plan"],
            state["plan_review"],
            state.get("additional_context", []),
            state.get("scenario_context", []),
        )
        response, records = complete_and_record(
            model,
            stage="implementation_plan_revision",
            system_prompt=PLAN_REVISION_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(state.get("additional_context", []), prompt),
        )
        plan = parse_model(response, ImplementationPlan)
        return {"implementation_plan": plan.model_dump(mode="json"), "context_records": records}

    def review_revised_plan(state: OrchestratorState) -> dict[str, object]:
        prompt = plan_review_request(
            state["task_spec"],
            state["implementation_plan"],
            merge_context(state["retrieved_context"], state.get("additional_context", [])),
            state.get("scenario_context", []),
        )
        response, records = complete_and_record(
            model,
            stage="implementation_plan_final_review",
            system_prompt=PLAN_REVIEW_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(
                merge_context(state["retrieved_context"], state.get("additional_context", [])),
                prompt,
            ),
        )
        review = parse_model(response, PlanReview)
        ready = review.ok and not review.search_queries
        return {
            "plan_review": review.model_dump(mode="json"),
            "context_records": records,
            "status": "plan_ready" if ready else "plan_rejected",
        }

    def route_after_final_review(state: OrchestratorState) -> str:
        review = PlanReview.model_validate(state["plan_review"])
        return "generate_code" if review.ok and not review.search_queries else "stop"

    def generate(state: OrchestratorState) -> dict[str, object]:
        context = merge_context(state["retrieved_context"], state.get("additional_context", []))
        prompt = code_request(
            state["task_spec"],
            context,
            state["implementation_plan"],
            state["plan_review"],
            state.get("scenario_context", []),
        )
        response, records = complete_and_record(
            model,
            stage="code_generation",
            system_prompt=CODE_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(
                merge_context(context, state.get("scenario_context", [])), prompt
            ),
        )
        code = sanitize_code(response)
        if not code:
            raise ValueError("Generation Agent returned empty code")
        return {"code": code, "context_records": records, "status": "generated", "fix_attempts": 0}

    def validate(state: OrchestratorState) -> dict[str, object]:
        result = validate_code(state["code"], TaskSpec.model_validate(state["task_spec"]))
        return {"code_validation": result.model_dump(mode="json")}

    builder = StateGraph(OrchestratorState)
    builder.add_node("plan_retrieval", plan_retrieval)
    builder.add_node("search_knowledge", retrieve)
    builder.add_node("create_implementation_plan", create_plan)
    builder.add_node("review_implementation_plan", review_plan)
    builder.add_node("search_missing_knowledge", retrieve_missing)
    builder.add_node("revise_implementation_plan", revise_plan)
    builder.add_node("review_revised_plan", review_revised_plan)
    builder.add_node("generate_code", generate)
    builder.add_node("validate_code", validate)
    builder.add_edge(START, "plan_retrieval")
    builder.add_edge("plan_retrieval", "search_knowledge")
    builder.add_edge("search_knowledge", "create_implementation_plan")
    builder.add_edge("create_implementation_plan", "review_implementation_plan")
    builder.add_conditional_edges(
        "review_implementation_plan",
        route_after_review,
        {
            "generate_code": "generate_code",
            "search_missing_knowledge": "search_missing_knowledge",
        },
    )
    builder.add_edge("search_missing_knowledge", "revise_implementation_plan")
    builder.add_edge("revise_implementation_plan", "review_revised_plan")
    builder.add_conditional_edges(
        "review_revised_plan",
        route_after_final_review,
        {"generate_code": "generate_code", "stop": END},
    )
    builder.add_edge("generate_code", "validate_code")
    builder.add_edge("validate_code", END)
    return builder.compile()
