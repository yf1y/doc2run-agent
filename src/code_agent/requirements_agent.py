from __future__ import annotations

from typing import Any

from .llm import TextModel
from .parsing import parse_model
from .prompts import REQUIREMENTS_SYSTEM, requirements_request
from .schemas import (
    REQUIRED_SECTIONS,
    ChatMessage,
    RequirementsDecision,
    SessionRecord,
    TaskSpec,
)


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


class RequirementsAgent:
    def __init__(self, model: TextModel) -> None:
        self.model = model

    def process(self, record: SessionRecord, user_input: str) -> SessionRecord:
        message = user_input.strip()
        if not message:
            raise ValueError("User input cannot be empty")
        record.messages.append(ChatMessage(role="user", content=message))

        response = self.model.complete(
            REQUIREMENTS_SYSTEM,
            requirements_request(
                record.draft_spec.model_dump(mode="json"),
                record.confirmed_sections,
                [item.model_dump(mode="json") for item in record.messages],
            ),
        )
        decision = parse_model(response, RequirementsDecision)
        record.draft_spec = apply_spec_patch(record.draft_spec, decision.spec_patch)
        record.confirmed_sections = _merge_confirmed_sections(
            record.confirmed_sections,
            list(decision.confirmed_sections),
        )

        missing = missing_sections(record.draft_spec, record.confirmed_sections)
        if missing:
            questions = decision.questions[:2] or fallback_questions(missing)[:2]
            record.draft_spec.status = "draft"
            record.draft_spec.unresolved_questions = questions
            record.pending_questions = questions
            record.phase = phase_for_section(missing[0])
            record.status = "collecting_requirements"
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
        return record


def apply_spec_patch(spec: TaskSpec, patch: dict[str, Any]) -> TaskSpec:
    unknown = set(patch) - PATCHABLE_FIELDS
    if unknown:
        raise ValueError(f"Requirements Agent returned unknown TaskSpec fields: {sorted(unknown)}")
    value = spec.model_dump(mode="json")
    value.update(patch)
    value["status"] = "draft"
    value["version"] = spec.version
    value["unresolved_questions"] = []
    return TaskSpec.model_validate(value)


def missing_sections(spec: TaskSpec, confirmed_sections: list[str]) -> list[str]:
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
    return missing


def phase_for_section(section: str) -> str:
    return {
        "goal": "collecting_goal",
        "inputs_outputs": "collecting_inputs_outputs",
        "constraints": "collecting_constraints",
        "acceptance": "collecting_acceptance",
    }[section]


def fallback_questions(missing: list[str]) -> list[str]:
    questions = {
        "goal": "What should the generated program accomplish?",
        "inputs_outputs": "What are the program inputs and required outputs?",
        "constraints": "Which APIs, dependencies, and side effects are allowed?",
        "acceptance": "What verifiable conditions determine that the result is correct?",
    }
    return [questions[section] for section in missing]


def _merge_confirmed_sections(current: list[str], additions: list[str]) -> list[str]:
    merged = set(current) | set(additions)
    return [section for section in REQUIRED_SECTIONS if section in merged]


def _compose_message(message: str, questions: list[str]) -> str:
    parts = [message.strip()] if message.strip() else []
    parts.extend(f"- {question}" for question in questions)
    return "\n".join(parts)
