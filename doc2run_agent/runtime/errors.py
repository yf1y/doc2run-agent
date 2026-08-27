"""Classify static-validation and execution failures for the Fix stage."""

from __future__ import annotations

import re

from ..schemas import CodeValidation, ErrorInfo, RunResult


def classify_failure(
    run_result: RunResult | None,
    validation: CodeValidation | None = None,
) -> ErrorInfo:
    if validation is not None and not validation.ok:
        return ErrorInfo(
            category="static_validation",
            exception_type="CodeValidationError",
            message="; ".join(validation.errors),
        )
    if run_result is None:
        return ErrorInfo(
            category="runtime_error",
            exception_type="MissingRunResult",
            message="No execution result is available",
        )
    if run_result.ok:
        return ErrorInfo(category="success")
    if run_result.timed_out:
        return ErrorInfo(
            category="timeout",
            exception_type="TimeoutExpired",
            message=run_result.stderr,
            traceback=run_result.stderr,
        )

    stderr = run_result.stderr.strip()
    exception_type, message = _last_exception(stderr)
    category = {
        "ModuleNotFoundError": "missing_dependency",
        "ImportError": "missing_dependency",
        "FileNotFoundError": "missing_input",
        "SyntaxError": "syntax_error",
        "IndentationError": "syntax_error",
    }.get(exception_type, "runtime_error")
    return ErrorInfo(
        category=category,
        exception_type=exception_type,
        message=message or stderr[-1000:],
        traceback=stderr[-6000:],
    )


def _last_exception(stderr: str) -> tuple[str, str]:
    for line in reversed(stderr.splitlines()):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s*(.*)$", line.strip())
        if match:
            return match.group(1), match.group(2)
    return "RuntimeError", stderr.splitlines()[-1] if stderr else "Execution failed"
