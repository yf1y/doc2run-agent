"""System prompts and prompt builders for the Chat, Code, and Fix stages."""

from __future__ import annotations

import json
from typing import Any

from .context import format_context, trim_run_result


CHAT_SYSTEM = """You are the Chat Agent for a documentation-grounded Python code generator.
Work incrementally. Read the selected Scene in full, the conversation, the current TaskSpec, and the
current Scenario Plan. Then return exactly one JSON object:
{
  "spec_patch": {"only_fields_that_changed": "..."},
  "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
  "questions": ["at most two essential follow-up questions"],
  "assistant_message": "a concise response before the questions",
  "decisions": ["important user decisions stated in this turn"],
  "scenario_plan": "the complete current Scenario Plan as structured Markdown"
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

The decisions list must contain only explicit user choices or corrections, not your inferences.
The Scenario Plan is the executable blueprint for this requested scene. It should explain concrete
arrangement, components, relationships, parameters, invariants, generalization rules, output, and
acceptance details when they are relevant. It may use any clear Markdown headings; these are not a
fixed schema. Return the complete plan on every turn, not a patch or summary. Use the selected Scene
as scenario knowledge, but adapt it to the user's requested scale and explicit decisions. Never
invent API names or signatures because API knowledge is intentionally unavailable during Chat.
Do not guess paths, formats, side effects, or acceptance criteria. Ask only one or two questions at
a time. Do not generate code. Do not include Markdown outside the JSON object."""


RETRIEVAL_PLAN_SYSTEM = """Plan the API documentation search for Doc2Run Agent.
Return exactly one JSON object with a queries array containing one to four focused searches.
Derive the searches from the confirmed Scenario Plan's components, actions, and required outputs.
Cover exact calls, data formats, setup, call order, and limits needed to implement that plan.
Prefer exact class, function, and parameter names when known. Do not generate code or Markdown."""


CODE_SYSTEM = """Generate one complete executable Python script for Doc2Run Agent.
Follow the confirmed TaskSpec and the complete confirmed Scenario Plan. Treat that plan as the source
of truth for arrangement, components, relationships, invariants, and generalization. Use the supplied
API signatures and examples exactly; do not invent undocumented APIs. Use only allowed dependencies
and write outputs only to relative paths under the current working directory. Return only Python
source code without fences."""


FIX_PLAN_SYSTEM = """Plan a small repair for a failed Python script.
Identify the problem, the code location to change, what should change, and behavior that must remain
unchanged. Include up to four focused documentation searches. Return exactly one JSON object:
{
  "problem": "plain explanation",
  "location": "function or code area",
  "change": "specific intended change",
  "keep_unchanged": ["working behavior to preserve"],
  "search_queries": ["targeted documentation query"]
}
Do not write the patch yet."""


PATCH_SYSTEM = """Repair only the relevant part of the current Python script.
Return exactly one JSON object with exact text replacements:
{
  "edits": [{"old": "exact text copied from current code", "new": "replacement text"}],
  "replacement_code": ""
}
Each old value must occur exactly once in the current code. Keep unrelated code unchanged.
Leave replacement_code empty. A full replacement_code is permitted only when the prompt explicitly
says that local edits have already failed. Do not include Markdown."""


PATCH_REVIEW_SYSTEM = """Check a proposed code repair before execution.
Verify that it follows the repair plan and documentation and preserves the TaskSpec, inputs, outputs,
and unrelated working behavior. Return exactly one JSON object:
{
  "ok": true,
  "checks": ["what was checked"],
  "problems": []
}
Do not return code or another patch."""


REFINEMENT_PLAN_SYSTEM = """Plan a focused change requested by the user for an already working script.
The confirmed TaskSpec is still the boundary: preserve its goal, inputs, outputs, dependencies, and
side effects, and preserve the confirmed Scenario Plan's arrangement, components, relationships,
invariants, and acceptance conditions. Return exactly one JSON object:
{
  "problem": "requested change or contract conflict",
  "location": "function or code area",
  "change": "specific intended change, or why a new plan is required",
  "keep_unchanged": ["working behavior to preserve"],
  "search_queries": ["targeted documentation query"],
  "contract_compatible": true
}
Set contract_compatible to false when the request changes the confirmed contract. Do not write code
for an incompatible request and do not silently reinterpret the Scenario Plan."""


def chat_request(
    draft_spec: dict[str, Any],
    confirmed_sections: list[str],
    messages: list[dict[str, Any]],
    decisions: list[str] | None = None,
    selected_scene: dict[str, Any] | None = None,
    scenario_plan: str = "",
) -> str:
    return (
        "Current TaskSpec draft:\n"
        + _json(draft_spec)
        + "\n\nAlready confirmed sections:\n"
        + _json(confirmed_sections)
        + "\n\nExplicit user decisions retained from earlier turns:\n"
        + _json(decisions or [])
        + "\n\nSelected Scene (one complete document; scenario knowledge only):\n"
        + _selected_scene(selected_scene)
        + "\n\nCurrent Scenario Plan draft:\n"
        + (scenario_plan or "(not written yet)")
        + "\n\nRecent conversation:\n"
        + _json(messages[-6:])
    )


def retrieval_plan_request(
    task_spec: dict[str, Any],
    scenario_plan: str,
    decisions: list[str] | None = None,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nConfirmed Scenario Plan:\n"
        + scenario_plan
        + "\n\nUser decisions:\n"
        + _json(decisions or [])
    )


def code_request(
    task_spec: dict[str, Any],
    api_context: list[dict[str, Any]],
    scenario_plan: str,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nConfirmed Scenario Plan (pass through unchanged):\n"
        + scenario_plan
        + "\n\nSelected API documentation:\n"
        + format_context(api_context)
    )


def fix_plan_request(
    task_spec: dict[str, Any],
    scenario_plan: str,
    code: str,
    error_info: dict[str, Any],
    run_result: dict[str, Any],
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nConfirmed Scenario Plan:\n"
        + scenario_plan
        + "\n\nCurrent code:\n"
        + code
        + "\n\nFailure:\n"
        + _json(error_info)
        + "\n\nExecution result (trimmed):\n"
        + _json(trim_run_result(run_result))
    )


def patch_request(
    task_spec: dict[str, Any],
    scenario_plan: str,
    fix_plan: dict[str, Any],
    context: list[dict[str, Any]],
    code: str,
    attempt: int,
) -> str:
    rewrite_instruction = (
        "Local edits have failed before; replacement_code may be used if an exact edit is not safe."
        if attempt >= 2
        else "Use exact edits only; replacement_code must remain empty."
    )
    return (
        f"Repair attempt: {attempt}\n{rewrite_instruction}\n\n"
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nConfirmed Scenario Plan:\n"
        + scenario_plan
        + "\n\nRepair plan:\n"
        + _json(fix_plan)
        + "\n\nCurrent code:\n"
        + code
        + "\n\nRelevant API documentation:\n"
        + format_context(context)
    )


def patch_review_request(
    task_spec: dict[str, Any],
    scenario_plan: str,
    fix_plan: dict[str, Any],
    before: str,
    after: str,
    patch_error: str,
    context: list[dict[str, Any]],
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nConfirmed Scenario Plan:\n"
        + scenario_plan
        + "\n\nRepair plan:\n"
        + _json(fix_plan)
        + "\n\nPatch application error:\n"
        + (patch_error or "(none)")
        + "\n\nCode before repair:\n"
        + before
        + "\n\nCode after repair:\n"
        + after
        + "\n\nRelevant API documentation:\n"
        + format_context(context)
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _selected_scene(scene: dict[str, Any] | None) -> str:
    if not scene:
        return "(no Scene is available; derive a new scenario only from explicit user input)"
    heading = f" — {scene['heading']}" if scene.get("heading") else ""
    return f"[Source: {scene['source']}{heading}]\n{scene['content']}"
