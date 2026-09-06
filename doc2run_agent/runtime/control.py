"""Shared deadlines and immediate diagnostics for one workflow invocation."""

from __future__ import annotations

import os
import math
import queue
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar


class TaskTimeoutError(RuntimeError):
    """The generation/refinement budget has been exhausted."""


@dataclass
class RunControl:
    deadline: float
    event_sink: Callable[[dict[str, Any]], None]
    context_sink: Callable[[dict[str, Any]], None]
    secrets: tuple[str, ...] = ()
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


_current: ContextVar[RunControl | None] = ContextVar("doc2run_control", default=None)
T = TypeVar("T")


@contextmanager
def run_scope(timeout_seconds: float, event_sink, context_sink, *, secrets=()):
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    control = RunControl(time.monotonic() + timeout_seconds, event_sink, context_sink, secrets)
    token = _current.set(control)
    try:
        yield control
    finally:
        with control.lock:
            control.closed = True
        _current.reset(token)


def remaining_seconds(limit: float) -> float:
    control = _current.get()
    if control is None:
        return limit
    remaining = control.deadline - time.monotonic()
    if control.closed or remaining <= 0:
        raise TaskTimeoutError("Task timeout reached; no further model calls or execution will start")
    return min(limit, remaining)


def call_with_timeout(function: Callable[[], T], timeout_seconds: float) -> T:
    """Bound a blocking call; abandoned model responses cannot resume the graph.

    A provider may continue its in-flight request until its own socket timeout.
    Only the model call runs in this daemon, never graph or artifact mutations.
    """
    timeout = remaining_seconds(timeout_seconds)
    result: queue.Queue = queue.Queue(maxsize=1)
    context = copy_context()

    def invoke():
        remaining_seconds(timeout_seconds)
        return function()

    def worker():
        try:
            result.put((True, context.run(invoke)))
        except BaseException as error:
            result.put((False, error))

    threading.Thread(target=worker, daemon=True, name="doc2run-model").start()
    try:
        ok, value = result.get(timeout=timeout)
    except queue.Empty:
        remaining_seconds(timeout_seconds)
        raise TimeoutError(f"Model request exceeded {timeout_seconds:g} seconds") from None
    remaining_seconds(timeout_seconds)
    if not ok:
        raise value
    return value


def emit_event(stage: str, status: str, **details: Any) -> None:
    control = _current.get()
    if control is not None:
        with control.lock:
            if not control.closed:
                control.event_sink({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "stage": stage, "status": status, **details,
                })


def save_context(record: dict[str, Any]) -> None:
    control = _current.get()
    if control is not None:
        with control.lock:
            if not control.closed:
                control.context_sink(record)


def redact(value: str, *extra_secrets: str) -> str:
    """Remove configured credentials from local diagnostics and error messages."""
    control = _current.get()
    secrets = list(extra_secrets) + list(control.secrets if control else ())
    secrets.extend(value for name, value in os.environ.items()
                   if any(part in name.upper() for part in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")))
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= 4:
            value = value.replace(secret, "[REDACTED]")
    return value
