"""Shared deterministic model responses and helpers for the test suite."""

from __future__ import annotations

from collections import deque
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


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


@pytest.fixture
def model_http_server():
    """A local provider endpoint, exercising the actual LiteLLM HTTP adapter."""
    replies = deque()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            status, text = replies.popleft() if replies else (500, "No test response left")
            value = ({"id": "local-test", "object": "chat.completion", "created": 0,
                      "model": "local-test", "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": text}}],
                      "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                     if status == 200 else {"error": {"message": text, "type": "test_error"}})
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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
