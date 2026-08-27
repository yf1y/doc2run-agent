"""Chat stage: select one Scene, clarify the request, and build the Scenario Plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..knowledge.tools import SceneSearchTool
from ..llm import TextModel
from ..schemas import (
    CONFIRMABLE_SECTIONS,
    ChatDecision,
    ChatMessage,
    SessionRecord,
    TaskSpec,
)
from .context import complete_and_record
from .parsing import parse_model
from .prompts import CHAT_SYSTEM, chat_request


PATCHABLE_FIELDS = {
    "objective",
    "inputs",
    "outputs",
    "steps",
    "constraints",
    "allowed_apis",
    "allowed_dependencies",
    "side_effects",
    "acceptance_criteria",
}


@dataclass(frozen=True)
class ChatTurn:
    """Return the updated session together with the model context from one Chat turn."""

    record: SessionRecord
    context_records: list[dict[str, Any]]


class ChatAgent:
    """Own the interactive requirement-clarification and plan-building stage."""

    def __init__(self, model: TextModel, scene_tool: SceneSearchTool | None = None) -> None:
        self.model = model
        self.scene_tool = scene_tool

    def process(self, record: SessionRecord, user_input: str) -> SessionRecord:
        """Process one Chat turn and return the updated session record."""

        return self.process_turn(record, user_input).record

    def process_turn(self, record: SessionRecord, user_input: str) -> ChatTurn:
        message = user_input.strip()
        if not message:
            raise ValueError("User input cannot be empty")
        if record.selected_scene is None and self.scene_tool is not None:
            record.selected_scene = self.scene_tool.select(message)
        record.messages.append(ChatMessage(role="user", content=message))

        user_prompt = chat_request(
            record.draft_spec.model_dump(mode="json"),
            record.confirmed_sections,
            [item.model_dump(mode="json") for item in record.messages],
            record.decisions,
            selected_scene=record.selected_scene,
            scenario_plan=record.draft_plan,
        )
        response, context_records = complete_and_record(
            self.model,
            stage="chat",
            system_prompt=CHAT_SYSTEM,
            user_prompt=user_prompt,
            sources=(
                [str(record.selected_scene["source"])] if record.selected_scene else []
            ),
        )
        decision = parse_model(response, ChatDecision)
        record.draft_spec = apply_spec_patch(record.draft_spec, decision.spec_patch)
        record.confirmed_sections = _merge_confirmed_sections(
            record.confirmed_sections,
            list(decision.confirmed_sections),
        )
        record.decisions = _merge_decisions(record.decisions, list(decision.decisions))
        if decision.scenario_plan:
            record.draft_plan = decision.scenario_plan

        missing = missing_sections(
            record.draft_spec, record.confirmed_sections, record.draft_plan
        )
        if missing:
            questions = decision.questions[:2] or fallback_questions(missing)[:2]
            record.draft_spec.status = "draft"
            record.draft_spec.unresolved_questions = questions
            record.pending_questions = questions
            record.phase = phase_for_section(missing[0])
            record.status = "chat"
            assistant_message = _compose_message(decision.assistant_message, questions)
        else:
            record.draft_spec.status = "ready_for_confirmation"
            record.draft_spec.unresolved_questions = []
            record.pending_questions = []
            record.phase = "awaiting_confirmation"
            record.status = "awaiting_confirmation"
            assistant_message = (
                decision.assistant_message.strip()
                or "The task specification is complete. Review it and enter /confirm to continue."
            )

        record.messages.append(ChatMessage(role="assistant", content=assistant_message))
        return ChatTurn(record=record, context_records=context_records)




def apply_spec_patch(spec: TaskSpec, patch: dict[str, Any]) -> TaskSpec:
    unknown = set(patch) - PATCHABLE_FIELDS
    if unknown:
        raise ValueError(f"Chat Agent returned unknown TaskSpec fields: {sorted(unknown)}")
    value = spec.model_dump(mode="json")
    value.update(patch)
    value["status"] = "draft"
    value["version"] = spec.version
    value["unresolved_questions"] = []
    return TaskSpec.model_validate(value)


def missing_sections(
    spec: TaskSpec, confirmed_sections: list[str], scenario_plan: str = ""
) -> list[str]:
    confirmed = set(confirmed_sections)
    missing: list[str] = []
    if "goal" not in confirmed or not spec.objective.strip():
        missing.append("goal")
    if "inputs_outputs" not in confirmed or not spec.outputs:
        missing.append("inputs_outputs")
    if "constraints" not in confirmed:
        missing.append("constraints")
    if "acceptance" not in confirmed or not spec.acceptance_criteria:
        missing.append("acceptance")
    if not is_structured_plan(scenario_plan):
        missing.append("scenario_plan")
    return missing


def phase_for_section(section: str) -> str:
    return {
        "goal": "collecting_goal",
        "inputs_outputs": "collecting_inputs_outputs",
        "constraints": "collecting_constraints",
        "acceptance": "collecting_acceptance",
        "scenario_plan": "collecting_plan",
    }[section]


def fallback_questions(missing: list[str]) -> list[str]:
    questions = {
        "goal": "What should the generated program accomplish?",
        "inputs_outputs": "What are the program inputs and required outputs?",
        "constraints": "Which APIs, dependencies, and side effects are allowed?",
        "acceptance": "What verifiable conditions determine that the result is correct?",
        "scenario_plan": "Please confirm the concrete arrangement, components, relationships, and generalization rules that must appear in the Scenario Plan.",
    }
    return [questions[section] for section in missing]


def _merge_confirmed_sections(current: list[str], additions: list[str]) -> list[str]:
    merged = set(current) | set(additions)
    return [section for section in CONFIRMABLE_SECTIONS if section in merged]


def _merge_decisions(current: list[str], additions: list[str]) -> list[str]:
    return list(dict.fromkeys([*current, *additions]))


def _compose_message(message: str, questions: list[str]) -> str:
    parts = [message.strip()] if message.strip() else []
    parts.extend(f"- {question}" for question in questions)
    return "\n".join(parts)


def is_structured_plan(value: str) -> bool:
    lines = [line.strip() for line in value.strip().splitlines() if line.strip()]
    has_heading = any(line.startswith("#") and line.lstrip("#").strip() for line in lines)
    has_body = any(not line.startswith("#") for line in lines)
    return has_heading and has_body
