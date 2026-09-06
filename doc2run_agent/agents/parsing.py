"""Strict parsing helpers for JSON objects returned by model-backed stages."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from ..llm import ModelResponseError, TextModel
from ..runtime.control import emit_event, redact
from .context import complete_and_record


ModelT = TypeVar("ModelT", bound=BaseModel)


def extract_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Model response did not contain a JSON object") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as error:
            raise ValueError(f"Model returned invalid JSON: {error.msg}") from None
    if not isinstance(parsed, dict):
        raise ValueError("Model response must be a JSON object")
    return parsed


def parse_model(value: str, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(extract_json_object(value))


def complete_structured(
    model: TextModel, model_type: type[ModelT], *, stage: str,
    system_prompt: str, user_prompt: str, current=None, sources=None,
    validate: Callable[[ModelT], Any] | None = None,
) -> tuple[ModelT, list[dict[str, Any]]]:
    """Allow one format correction, keeping the original task and schema."""
    prompt = user_prompt
    records = current
    for attempt in range(2):
        try:
            response, records = complete_and_record(
                model, stage=stage if attempt == 0 else f"{stage}_format_retry",
                system_prompt=system_prompt, user_prompt=prompt, current=records, sources=sources,
            )
        except ModelResponseError:
            response = ""
        try:
            value = parse_model(response, model_type)
            if validate is not None:
                validate(value)
            return value, records
        except ValueError as error:
            detail = (json.dumps(error.errors(include_input=False, include_context=False, include_url=False))
                      if isinstance(error, ValidationError) else str(error))
            detail = redact(detail)[:2000]
            emit_event(stage, "invalid_format", attempt=attempt + 1, error=detail)
            if attempt == 1:
                raise ValueError(f"{stage}: model returned invalid {model_type.__name__} JSON twice: {detail}; see contexts/") from None
            prompt = (
                user_prompt + "\n\nYour previous reply was not a valid JSON object for this stage. "
                "Return only the required JSON; preserve the user's original requirements. "
                "Validation error: " + detail + "\nFollow this JSON schema:\n"
                + json.dumps(model_type.model_json_schema(), ensure_ascii=False)
            )
    raise AssertionError("unreachable")
