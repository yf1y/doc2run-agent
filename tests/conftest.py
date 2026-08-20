from __future__ import annotations

from collections import deque


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

