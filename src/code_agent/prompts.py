from __future__ import annotations

import json
from typing import Any


REQUIREMENTS_SYSTEM = """You are the Requirements Agent for a documentation-grounded Python code generator.
Work incrementally. Read the conversation and current TaskSpec, then return exactly one JSON object:
{
  "spec_patch": {"only_fields_that_changed": "..."},
  "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
  "questions": ["at most two essential follow-up questions"],
  "assistant_message": "a concise response before the questions"
}

TaskSpec fields allowed in spec_patch:
objective, inputs, outputs, steps, constraints, allowed_apis, allowed_dependencies,
side_effects, acceptance_criteria.

Input objects require name, type, source, and optional required.
Output objects require name, format, and destination.

Mark a section confirmed only when the user explicitly supplied or accepted it:
- goal: the objective is clear
- inputs_outputs: inputs and outputs are clear; inputs may be an empty list
- constraints: dependencies, allowed APIs, and side effects are clear; lists may be empty
- acceptance: at least one verifiable acceptance criterion is clear

Do not guess paths, APIs, formats, side effects, or acceptance criteria. Ask only one or two
questions at a time. Do not generate code. Do not include Markdown outside the JSON object."""


RETRIEVAL_PLAN_SYSTEM = """You plan knowledge retrieval for a Python Code Agent.
Return exactly one JSON object with a queries array containing one or two focused searches.
Queries should target API names, signatures, examples, or constraints needed for the confirmed task.
Do not generate code and do not include Markdown."""


CODE_SYSTEM = """You are the Code Agent in a documentation-grounded Python workflow.
Generate one complete executable Python script that satisfies the confirmed TaskSpec.
Use retrieved documentation as grounding. Do not invent APIs that contradict the documentation.
Use only allowed dependencies. Write outputs only to relative paths under the current working directory.
Return only Python source code, without Markdown fences or explanation."""


FIX_RETRIEVAL_SYSTEM = """You plan targeted knowledge retrieval for a Python Fix Agent.
Given the confirmed TaskSpec, current code, and classified failure, return exactly one JSON object
with a queries array containing one or two searches likely to resolve the failure.
Do not propose a fix and do not include Markdown."""


FIX_SYSTEM = """You are the Fix Agent in a documentation-grounded Python workflow.
Repair the complete script using the confirmed TaskSpec, classified error, execution output,
and retrieved documentation. Preserve intended behavior, obey allowed dependencies, and write only
to relative paths under the current working directory. Return the entire corrected Python script,
without Markdown fences, diffs, or explanation."""


def requirements_request(
    draft_spec: dict[str, Any],
    confirmed_sections: list[str],
    messages: list[dict[str, Any]],
) -> str:
    return (
        "Current TaskSpec draft:\n"
        + json.dumps(draft_spec, ensure_ascii=False, indent=2)
        + "\n\nAlready confirmed sections:\n"
        + json.dumps(confirmed_sections, ensure_ascii=False)
        + "\n\nRecent conversation:\n"
        + json.dumps(messages[-12:], ensure_ascii=False, indent=2)
    )


def retrieval_plan_request(task_spec: dict[str, Any]) -> str:
    return "Confirmed TaskSpec:\n" + json.dumps(task_spec, ensure_ascii=False, indent=2)


def code_request(task_spec: dict[str, Any], context: list[dict[str, Any]]) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + json.dumps(task_spec, ensure_ascii=False, indent=2)
        + "\n\nRetrieved documentation:\n"
        + format_context(context)
    )


def fix_retrieval_request(
    task_spec: dict[str, Any],
    code: str,
    error_info: dict[str, Any],
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + json.dumps(task_spec, ensure_ascii=False, indent=2)
        + "\n\nCurrent code:\n"
        + code
        + "\n\nClassified failure:\n"
        + json.dumps(error_info, ensure_ascii=False, indent=2)
    )


def fix_request(
    task_spec: dict[str, Any],
    context: list[dict[str, Any]],
    code: str,
    run_result: dict[str, Any],
    error_info: dict[str, Any],
    attempt: int,
) -> str:
    return (
        f"Repair attempt: {attempt}\n\n"
        "Confirmed TaskSpec:\n"
        + json.dumps(task_spec, ensure_ascii=False, indent=2)
        + "\n\nCurrent code:\n"
        + code
        + "\n\nClassified failure:\n"
        + json.dumps(error_info, ensure_ascii=False, indent=2)
        + "\n\nExecution or validation result:\n"
        + json.dumps(run_result, ensure_ascii=False, indent=2)
        + "\n\nRetrieved documentation:\n"
        + format_context(context)
    )


def format_context(context: list[dict[str, Any]]) -> str:
    if not context:
        return "(no documentation retrieved)"
    return "\n\n".join(
        f"[Source: {item['source']}]\n{item['content']}" for item in context
    )
