from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


REQUIRED_SECTIONS = ("goal", "inputs_outputs", "constraints", "acceptance")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputSpec(StrictModel):
    name: str
    type: str
    source: str
    required: bool = True


class OutputSpec(StrictModel):
    name: str
    format: str
    destination: str


class TaskSpec(StrictModel):
    objective: str = ""
    inputs: list[InputSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    allowed_apis: list[str] = Field(default_factory=list)
    allowed_dependencies: list[str] = Field(default_factory=lambda: ["standard-library"])
    side_effects: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    status: Literal["draft", "ready_for_confirmation", "confirmed"] = "draft"
    version: int = 0

    @field_validator(
        "steps",
        "constraints",
        "allowed_apis",
        "allowed_dependencies",
        "side_effects",
        "acceptance_criteria",
        "unresolved_questions",
    )
    @classmethod
    def remove_blank_items(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class RequirementsDecision(StrictModel):
    spec_patch: dict[str, Any] = Field(default_factory=dict)
    confirmed_sections: list[Literal["goal", "inputs_outputs", "constraints", "acceptance"]] = (
        Field(default_factory=list)
    )
    questions: list[str] = Field(default_factory=list)
    assistant_message: str = ""

    @field_validator("questions")
    @classmethod
    def clean_questions(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class RetrievalQueryPlan(StrictModel):
    queries: list[str]

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("At least one retrieval query is required")
        return cleaned[:2]


class CodeValidation(StrictModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)


class RunResult(StrictModel):
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ErrorInfo(StrictModel):
    category: Literal[
        "success",
        "static_validation",
        "timeout",
        "missing_dependency",
        "missing_input",
        "syntax_error",
        "runtime_error",
    ]
    exception_type: str = ""
    message: str = ""
    traceback: str = ""


class ChatMessage(StrictModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str = Field(default_factory=utc_now)


class SessionRecord(StrictModel):
    session_id: str
    phase: Literal[
        "collecting_goal",
        "collecting_inputs_outputs",
        "collecting_constraints",
        "collecting_acceptance",
        "awaiting_confirmation",
        "generating_code",
        "executing",
        "repairing",
        "succeeded",
        "failed",
    ] = "collecting_goal"
    messages: list[ChatMessage] = Field(default_factory=list)
    draft_spec: TaskSpec = Field(default_factory=TaskSpec)
    confirmed_spec: TaskSpec | None = None
    confirmed_sections: list[str] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    retrieved_context: list[dict[str, Any]] = Field(default_factory=list)
    generated_code: str = ""
    code_validation: CodeValidation | None = None
    run_result: RunResult | None = None
    run_history: list[dict[str, Any]] = Field(default_factory=list)
    fix_attempts: int = 0
    status: str = "collecting_requirements"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class OrchestratorState(TypedDict, total=False):
    event: Literal["message", "confirm"]
    user_input: str
    session: dict[str, Any]
    task_spec: dict[str, Any]
    retrieval_queries: list[str]
    retrieved_context: list[dict[str, Any]]
    fix_context: list[dict[str, Any]]
    code: str
    code_validation: dict[str, Any]
    run_result: dict[str, Any]
    run_history: list[dict[str, Any]]
    fix_attempts: int
    error_info: dict[str, Any]
    status: str
    assistant_message: str
    artifact_paths: list[str]


WorkflowState = OrchestratorState
