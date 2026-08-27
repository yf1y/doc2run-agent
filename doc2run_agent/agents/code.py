"""Code stage graph for API retrieval, code generation, and static validation."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..knowledge.tools import KnowledgeSearchTool
from ..llm import TextModel
from ..runtime.runner import sanitize_code
from ..runtime.validation import validate_code
from ..schemas import OrchestratorState, RetrievalQueryPlan, TaskSpec
from .context import complete_and_record, context_sources
from .parsing import parse_model
from .prompts import CODE_SYSTEM, RETRIEVAL_PLAN_SYSTEM, code_request, retrieval_plan_request


def build_code_agent_graph(
    model: TextModel,
    knowledge_tool: KnowledgeSearchTool,
):
    """Run the Code stage: search API knowledge, generate, and validate code."""

    def plan_retrieval(state: OrchestratorState) -> dict[str, object]:
        prompt = retrieval_plan_request(
            state["task_spec"], state.get("scenario_plan", ""), state.get("decisions", [])
        )
        response, records = complete_and_record(
            model,
            stage="api_retrieval_plan",
            system_prompt=RETRIEVAL_PLAN_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
        )
        plan = parse_model(response, RetrievalQueryPlan)
        return {
            "retrieval_queries": plan.queries,
            "context_records": records,
            "status": "retrieving_api_context",
        }

    def retrieve(state: OrchestratorState) -> dict[str, object]:
        return {"retrieved_context": knowledge_tool.search_many(state["retrieval_queries"])}

    def generate(state: OrchestratorState) -> dict[str, object]:
        prompt = code_request(
            state["task_spec"], state["retrieved_context"], state.get("scenario_plan", "")
        )
        response, records = complete_and_record(
            model,
            stage="code_generation",
            system_prompt=CODE_SYSTEM,
            user_prompt=prompt,
            current=state.get("context_records"),
            sources=context_sources(state["retrieved_context"], prompt),
        )
        code = sanitize_code(response)
        if not code:
            raise ValueError("Code Agent returned empty code")
        return {"code": code, "context_records": records, "status": "generated", "fix_attempts": 0}

    def validate(state: OrchestratorState) -> dict[str, object]:
        result = validate_code(
            state["code"],
            TaskSpec.model_validate(state["task_spec"]),
            state.get("retrieved_context", []),
        )
        return {"code_validation": result.model_dump(mode="json")}

    builder = StateGraph(OrchestratorState)
    builder.add_node("plan_api_retrieval", plan_retrieval)
    builder.add_node("search_api_knowledge", retrieve)
    builder.add_node("generate_code", generate)
    builder.add_node("validate_code", validate)
    builder.add_edge(START, "plan_api_retrieval")
    builder.add_edge("plan_api_retrieval", "search_api_knowledge")
    builder.add_edge("search_api_knowledge", "generate_code")
    builder.add_edge("generate_code", "validate_code")
    builder.add_edge("validate_code", END)
    return builder.compile()
