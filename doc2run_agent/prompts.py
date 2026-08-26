from __future__ import annotations

import json
from typing import Any

from .context import estimate_tokens, trim_run_result


REQUIREMENTS_SYSTEM = """You are the Requirements Agent for a documentation-grounded Python code generator.
Work incrementally. Read the conversation and current TaskSpec, then return exactly one JSON object:
{
  "spec_patch": {"only_fields_that_changed": "..."},
  "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
  "questions": ["at most two essential follow-up questions"],
  "assistant_message": "a concise response before the questions",
  "decisions": ["important user decisions stated in this turn"]
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
Do not guess paths, APIs, formats, side effects, or acceptance criteria. Ask only one or two
questions at a time. Do not generate code. Do not include Markdown outside the JSON object."""


RETRIEVAL_PLAN_SYSTEM = """Plan the first documentation search for Doc2Run Agent.
Return exactly one JSON object with a queries array containing one to four focused searches.
The same queries will be run separately against API documentation and the selected domain's reference
material. Cover exact calls, data formats, and business facts needed by the confirmed task.
Prefer exact class, function, and parameter names when known. Do not generate code or Markdown."""


IMPLEMENTATION_PLAN_SYSTEM = """Write an implementation plan before any code is generated.
Use only the confirmed task and supplied documentation. Separate documented facts from information
that is still missing. Return exactly one JSON object:
{
  "summary": "what the program will do",
  "required_inputs": ["data the program needs"],
  "steps": ["ordered implementation steps"],
  "api_usage": [{"purpose": "why it is used", "api": "exact API name", "source": "document source"}],
  "design_choices": ["details you are choosing because the task permits a new design"],
  "missing_information": ["facts needed but not present in the supplied material"]
}
API names, signatures, and exact named reference data must come from the task or documents. When the
task permits designing a new valid result, you may use general domain knowledge for the construction
details, but list every such choice in design_choices. Do not present a model choice as documented fact.
Do not write code. Do not treat an API example as a complete domain scenario."""


PLAN_REVIEW_SYSTEM = """Check an implementation plan before code generation.
Compare it with the confirmed task and documentation. Look for missing data, unsupported API calls,
incorrect call order, and steps that are too vague for a smaller code model. Check that model-made
design choices are allowed by the task and are not pretending to be exact reference data.
Return exactly one JSON object:
{
  "ok": true,
  "problems": [],
  "search_queries": []
}
Set ok to false when the plan needs revision. Put up to four targeted searches in search_queries
when more documentation could resolve a problem. Do not write code."""


PLAN_REVISION_SYSTEM = """Revise an implementation plan using its review and any additional documents.
Return exactly one complete implementation-plan JSON object with the fields summary, required_inputs,
steps, api_usage, design_choices, and missing_information. Remove problems that the new documents
resolve. A permitted new design may use general domain knowledge when its choices are listed clearly;
keep exact facts that are still unavailable in missing_information. Do not write code."""


CODE_SYSTEM = """Generate one complete executable Python script for Doc2Run Agent.
Follow the confirmed TaskSpec and the reviewed implementation plan. Use the supplied API signatures
and examples exactly; do not invent undocumented APIs. If the plan marks information as missing,
do not silently invent exact reference data. Use only allowed dependencies and write outputs only
to relative paths under the current working directory. Return only Python source code without fences."""


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
side effects. If the request conflicts with that specification, describe the conflict instead of
silently changing the contract. Return the same JSON shape as a repair plan: problem, location,
change, keep_unchanged, and search_queries. Do not write code yet."""


MEMORY_EXTRACT_SYSTEM = """Extract reusable scenario data from a user-approved result.
This is not API documentation and not a code summary. Return exactly one JSON object:
{
  "scenario_kind": "the exact kind required by the supplied domain schema",
  "scenario_name": "a short specific name",
  "summary": "what real scenario this data represents",
  "data": {"only fields allowed by the domain schema": "JSON values"}
}
Store only facts and data that describe this scenario. Never store source code, imports, function or
method names, API signatures, credentials, repair history, errors, or claims generalized from one
example. If the approved artifacts do not support a field, omit it unless the schema requires it.
Do not include Markdown."""


MEMORY_REVIEW_SYSTEM = """Independently review a proposed scenario-memory entry.
Check it against the domain schema and the final approved TaskSpec, plan, code, and output. Reject API
knowledge, code details, repair history, unsupported facts, and general rules inferred from a single
case. Do not correct or rewrite the candidate. Return exactly one JSON object:
{"ok": true, "problems": [], "summary": "short verdict"}
Do not include Markdown."""


# Compatibility names for integrations that imported the old prompt constants.
FIX_RETRIEVAL_SYSTEM = FIX_PLAN_SYSTEM
FIX_SYSTEM = PATCH_SYSTEM


def requirements_request(
    draft_spec: dict[str, Any],
    confirmed_sections: list[str],
    messages: list[dict[str, Any]],
    decisions: list[str] | None = None,
) -> str:
    return (
        "Current TaskSpec draft:\n"
        + _json(draft_spec)
        + "\n\nAlready confirmed sections:\n"
        + _json(confirmed_sections)
        + "\n\nExplicit user decisions retained from earlier turns:\n"
        + _json(decisions or [])
        + "\n\nRecent conversation:\n"
        + _json(messages[-6:])
    )


def retrieval_plan_request(task_spec: dict[str, Any], decisions: list[str] | None = None) -> str:
    return "Confirmed TaskSpec:\n" + _json(task_spec) + "\n\nUser decisions:\n" + _json(decisions or [])


def implementation_plan_request(
    task_spec: dict[str, Any],
    api_context: list[dict[str, Any]],
    scenario_context: list[dict[str, Any]] | None = None,
    domain_context: list[dict[str, Any]] | None = None,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nSelected API documentation (how to call the SDK/API):\n"
        + format_context(api_context)
        + "\n\nSelected domain documentation (business facts and rules):\n"
        + format_context(domain_context or [])
        + "\n\nApproved examples from this exact domain (scenario data only):\n"
        + format_context(scenario_context or [])
    )


def plan_review_request(
    task_spec: dict[str, Any],
    plan: dict[str, Any],
    api_context: list[dict[str, Any]],
    scenario_context: list[dict[str, Any]] | None = None,
    domain_context: list[dict[str, Any]] | None = None,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nImplementation plan:\n"
        + _json(plan)
        + "\n\nAPI documentation used by the plan:\n"
        + format_context(api_context)
        + "\n\nDomain documentation used by the plan:\n"
        + format_context(domain_context or [])
        + "\n\nApproved same-domain scenario examples:\n"
        + format_context(scenario_context or [])
    )


def plan_revision_request(
    task_spec: dict[str, Any],
    plan: dict[str, Any],
    review: dict[str, Any],
    additional_context: list[dict[str, Any]],
    scenario_context: list[dict[str, Any]] | None = None,
    domain_context: list[dict[str, Any]] | None = None,
    additional_domain_context: list[dict[str, Any]] | None = None,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nPrevious implementation plan:\n"
        + _json(plan)
        + "\n\nPlan review:\n"
        + _json(review)
        + "\n\nAdditional API documentation:\n"
        + format_context(additional_context)
        + "\n\nDomain documentation:\n"
        + format_context(domain_context or [])
        + "\n\nAdditional domain documentation:\n"
        + format_context(additional_domain_context or [])
        + "\n\nApproved same-domain scenario examples:\n"
        + format_context(scenario_context or [])
    )


def code_request(
    task_spec: dict[str, Any],
    api_context: list[dict[str, Any]],
    implementation_plan: dict[str, Any] | None = None,
    plan_review: dict[str, Any] | None = None,
    scenario_context: list[dict[str, Any]] | None = None,
    domain_context: list[dict[str, Any]] | None = None,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nReviewed implementation plan:\n"
        + _json(implementation_plan or {})
        + "\n\nFinal plan review:\n"
        + _json(plan_review or {})
        + "\n\nSelected API documentation:\n"
        + format_context(api_context)
        + "\n\nSelected domain documentation:\n"
        + format_context(domain_context or [])
        + "\n\nApproved same-domain scenario examples:\n"
        + format_context(scenario_context or [])
    )


def memory_extract_request(
    *,
    domain: str,
    schema: dict[str, Any],
    task_spec: dict[str, Any],
    implementation_plan: dict[str, Any],
    code: str,
    run_result: dict[str, Any],
    approval_note: str,
) -> str:
    return (
        "Active domain:\n" + domain
        + "\n\nHard domain memory schema:\n" + _json(schema)
        + "\n\nFinal approved TaskSpec:\n" + _json(task_spec)
        + "\n\nFinal implementation plan:\n" + _json(implementation_plan)
        + "\n\nFinal approved code:\n" + code
        + "\n\nFinal successful output:\n" + _json(trim_run_result(run_result))
        + "\n\nUser approval note:\n" + (approval_note or "(none)")
    )


def memory_review_request(
    *,
    schema: dict[str, Any],
    candidate: dict[str, Any],
    validation_errors: list[str],
    task_spec: dict[str, Any],
    implementation_plan: dict[str, Any],
    code: str,
    run_result: dict[str, Any],
) -> str:
    return (
        "Hard domain memory schema:\n" + _json(schema)
        + "\n\nCandidate (review only; do not rewrite):\n" + _json(candidate)
        + "\n\nDeterministic validation errors:\n" + _json(validation_errors)
        + "\n\nFinal approved TaskSpec:\n" + _json(task_spec)
        + "\n\nFinal implementation plan:\n" + _json(implementation_plan)
        + "\n\nFinal approved code:\n" + code
        + "\n\nFinal successful output:\n" + _json(trim_run_result(run_result))
    )


def fix_plan_request(
    task_spec: dict[str, Any],
    implementation_plan: dict[str, Any],
    code: str,
    error_info: dict[str, Any],
    run_result: dict[str, Any],
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nImplementation plan:\n"
        + _json(implementation_plan)
        + "\n\nCurrent code:\n"
        + code
        + "\n\nFailure:\n"
        + _json(error_info)
        + "\n\nExecution result (trimmed):\n"
        + _json(trim_run_result(run_result))
    )


def patch_request(
    task_spec: dict[str, Any],
    implementation_plan: dict[str, Any],
    fix_plan: dict[str, Any],
    context: list[dict[str, Any]],
    code: str,
    attempt: int,
    domain_context: list[dict[str, Any]] | None = None,
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
        + "\n\nImplementation plan:\n"
        + _json(implementation_plan)
        + "\n\nRepair plan:\n"
        + _json(fix_plan)
        + "\n\nCurrent code:\n"
        + code
        + "\n\nRelevant API documentation:\n"
        + format_context(context)
        + "\n\nRelevant domain documentation:\n"
        + format_context(domain_context or [])
    )


def patch_review_request(
    task_spec: dict[str, Any],
    implementation_plan: dict[str, Any],
    fix_plan: dict[str, Any],
    before: str,
    after: str,
    patch_error: str,
    context: list[dict[str, Any]],
    domain_context: list[dict[str, Any]] | None = None,
) -> str:
    return (
        "Confirmed TaskSpec:\n"
        + _json(task_spec)
        + "\n\nImplementation plan:\n"
        + _json(implementation_plan)
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
        + "\n\nRelevant domain documentation:\n"
        + format_context(domain_context or [])
    )


def fix_retrieval_request(task_spec: dict[str, Any], code: str, error_info: dict[str, Any]) -> str:
    return fix_plan_request(task_spec, {}, code, error_info, {})


def fix_request(
    task_spec: dict[str, Any],
    context: list[dict[str, Any]],
    code: str,
    run_result: dict[str, Any],
    error_info: dict[str, Any],
    attempt: int,
) -> str:
    fallback_plan = {
        "problem": error_info.get("message", "Execution failed"),
        "location": "current script",
        "change": "repair the reported failure",
        "keep_unchanged": [],
        "search_queries": [],
    }
    return patch_request(task_spec, {}, fallback_plan, context, code, attempt)


def format_context(context: list[dict[str, Any]], *, max_tokens: int = 6_000) -> str:
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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
