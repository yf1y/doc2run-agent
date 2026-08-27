"""Strict parsing helpers for JSON objects returned by model-backed stages."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel


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
