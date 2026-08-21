from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .knowledge_tools import KnowledgeSearchTool
from .llm import TextModel
from .parsing import parse_model
from .prompts import CODE_SYSTEM, RETRIEVAL_PLAN_SYSTEM, code_request, retrieval_plan_request
from .runner import sanitize_code
from .schemas import OrchestratorState, RetrievalQueryPlan, TaskSpec
from .validation import validate_code


def build_generation_agent_graph(model: TextModel, knowledge_tool: KnowledgeSearchTool):
    def plan_retrieval(state: OrchestratorState) -> dict[str, object]:
        plan = parse_model(
            model.complete(RETRIEVAL_PLAN_SYSTEM, retrieval_plan_request(state["task_spec"])),
            RetrievalQueryPlan,
        )
        return {"retrieval_queries": plan.queries, "status": "retrieving_code_context"}

    def retrieve(state: OrchestratorState) -> dict[str, object]:
        context = knowledge_tool.search_many(state["retrieval_queries"])
        return {"retrieved_context": context}

    def generate(state: OrchestratorState) -> dict[str, object]:
        code = sanitize_code(
            model.complete(CODE_SYSTEM, code_request(state["task_spec"], state["retrieved_context"]))
        )
        if not code:
            raise ValueError("Generation Agent returned empty code")
        return {"code": code, "status": "generated", "fix_attempts": 0}

    def validate(state: OrchestratorState) -> dict[str, object]:
        result = validate_code(state["code"], TaskSpec.model_validate(state["task_spec"]))
        return {"code_validation": result.model_dump(mode="json")}

    builder = StateGraph(OrchestratorState)
    builder.add_node("plan_retrieval", plan_retrieval)
    builder.add_node("search_knowledge", retrieve)
    builder.add_node("generate_code", generate)
    builder.add_node("validate_code", validate)
    builder.add_edge(START, "plan_retrieval")
    builder.add_edge("plan_retrieval", "search_knowledge")
    builder.add_edge("search_knowledge", "generate_code")
    builder.add_edge("generate_code", "validate_code")
    builder.add_edge("validate_code", END)
    return builder.compile()
