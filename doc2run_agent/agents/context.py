"""Shared model-context budgeting, recording, merging, and prompt formatting."""

from __future__ import annotations

import math
from typing import Any

from ..llm import TextModel
from ..schemas import ModelContextRecord


def estimate_tokens(text: str) -> int:
    """Return a conservative provider-neutral token estimate.

    Latin text is usually several characters per token while CJK text is much
    closer to one character per token.  The estimate is intentionally simple so
    context control does not depend on a particular model tokenizer.
    """

    ascii_characters = sum(1 for character in text if ord(character) < 128)
    non_ascii_characters = len(text) - ascii_characters
    return max(1, math.ceil(ascii_characters / 4) + non_ascii_characters)


def complete_and_record(
    model: TextModel,
    *,
    stage: str,
    system_prompt: str,
    user_prompt: str,
    current: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    input_tokens = estimate_tokens(system_prompt + "\n" + user_prompt)
    settings = getattr(model, "settings", None)
    context_limit = int(getattr(settings, "context_tokens", 16_000))
    output_reserve = int(getattr(settings, "max_tokens", 0))
    input_limit = context_limit - output_reserve
    if input_limit < 1:
        raise ValueError(
            f"The {stage} model configuration leaves no input space: "
            f"context_tokens={context_limit}, max_tokens={output_reserve}"
        )
    if input_tokens > input_limit:
        raise ValueError(
            f"The {stage} input is about {input_tokens} tokens, above its "
            f"configured {input_limit}-token input budget after reserving "
            f"{output_reserve} output tokens"
        )
    response = model.complete(system_prompt, user_prompt)
    record = ModelContextRecord(
        stage=stage,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response=response,
        estimated_tokens=input_tokens,
        sources=sources or [],
    )
    values = list(current or [])
    values.append(record.model_dump(mode="json"))
    return response, values


def context_sources(context: list[dict[str, Any]], prompt: str | None = None) -> list[str]:
    sources = [str(item.get("source", "")) for item in context if item.get("source")]
    if prompt is not None:
        sources = [source for source in sources if source in prompt]
    return list(dict.fromkeys(sources))


def merge_context(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group:
            source = str(item.get("source", ""))
            fingerprint = " ".join(str(item.get("content", "")).split())
            key = (source, fingerprint)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def format_context(context: list[dict[str, Any]], *, max_tokens: int = 6_000) -> str:
    """Format retrieved documents while enforcing the shared model-prompt budget."""

    if not context:
        return "(no documentation retrieved)"
    parts: list[str] = []
    omitted_sources: list[str] = []
    used_tokens = 0
    for item in context:
        heading = f" — {item['heading']}" if item.get("heading") else ""
        part = f"[Source: {item['source']}{heading}]\n{item['content']}"
        part_tokens = estimate_tokens(part)
        if used_tokens + part_tokens > max_tokens:
            omitted_sources.append(str(item["source"]))
            continue
        parts.append(part)
        used_tokens += part_tokens
    if omitted_sources:
        parts.append(
            "[Omitted because the documentation budget was reached: "
            + ", ".join(omitted_sources)
            + "]"
        )
    return "\n\n".join(parts) or "(documentation omitted by context limit)"


def trim_run_result(run_result: dict[str, Any], *, stdout_limit: int = 2000, stderr_limit: int = 6000) -> dict[str, Any]:
    value = dict(run_result)
    value["stdout"] = _head_and_tail(str(value.get("stdout", "")), stdout_limit)
    value["stderr"] = _head_and_tail(str(value.get("stderr", "")), stderr_limit, tail_bias=True)
    return value


def _head_and_tail(value: str, limit: int, *, tail_bias: bool = False) -> str:
    if len(value) <= limit:
        return value
    if tail_bias:
        head = max(200, limit // 4)
    else:
        head = limit // 2
    tail = limit - head
    omitted = len(value) - limit
    return f"{value[:head]}\n... [{omitted} characters omitted] ...\n{value[-tail:]}"
