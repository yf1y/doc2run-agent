from __future__ import annotations

from collections import deque
import json


class FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("FakeModel has no response left")
        return self.responses.popleft()


def task_spec_json() -> str:
    return (
        '{"objective":"Print a greeting","inputs":[],"constraints":'
        '["Use Python"],"expected_output":"The word hello"}'
    )


def implementation_plan_response() -> str:
    return json.dumps(
        {
            "summary": "Produce the requested output",
            "required_inputs": [],
            "steps": ["Build the result", "Print JSON"],
            "api_usage": [],
            "missing_information": [],
        }
    )


def plan_review_response(*, ok: bool = True, queries: list[str] | None = None) -> str:
    return json.dumps(
        {
            "ok": ok,
            "problems": [] if ok else ["More documentation is needed"],
            "search_queries": queries or [],
        }
    )


def fix_plan_response() -> str:
    return json.dumps(
        {
            "problem": "The script raises RuntimeError",
            "location": "top-level statement",
            "change": "Replace the raise statement with the required output",
            "keep_unchanged": ["output remains JSON"],
            "search_queries": ["JSON serialization"],
        }
    )


def patch_response(old: str, new: str) -> str:
    return json.dumps({"edits": [{"old": old, "new": new}], "replacement_code": ""})


def patch_review_response(*, ok: bool = True) -> str:
    return json.dumps(
        {
            "ok": ok,
            "checks": ["The requested local change was applied"],
            "problems": [] if ok else ["The patch is incorrect"],
        }
    )
