"""Shared deterministic model responses and helpers for the test suite."""

from __future__ import annotations

from collections import deque
import json


class FakeModel:
    """Minimal queued-response model used to test agents without network calls."""

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


def scenario_plan_text() -> str:
    return (
        "# 场景目标\n\n生成 JSON 输出。\n\n"
        "# 器件清单\n\n- 一个结果对象\n\n"
        "# 排布与连接关系\n\n- 构造结果并输出到 stdout\n\n"
        "# 输出与验收标准\n\n- stdout 是有效 JSON\n"
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
