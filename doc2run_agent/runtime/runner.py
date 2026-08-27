"""Run generated Python with a timeout and an explicit environment allowlist."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from ..schemas import RunResult


def sanitize_code(value: str) -> str:
    code = value.strip()
    if "</think>" in code:
        code = code.split("</think>", 1)[1].strip()

    fenced = re.fullmatch(r"```(?:python|py)?\s*(.*?)\s*```", code, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        code = fenced.group(1).strip()
    return code + "\n" if code else ""


class LocalPythonRunner:
    """Execute one generated script in a controlled working directory."""

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        *,
        environment_keys: Iterable[str] = (),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.environment_keys = _validate_environment_keys(environment_keys)

    def run(self, code: str, working_directory: str | Path | None = None) -> RunResult:
        clean_code = sanitize_code(code)
        started = time.monotonic()
        if working_directory is None:
            with tempfile.TemporaryDirectory(prefix="doc2run-agent-") as temporary:
                return self._run_in_directory(clean_code, Path(temporary), started)
        directory = Path(working_directory)
        directory.mkdir(parents=True, exist_ok=True)
        return self._run_in_directory(clean_code, directory, started)

    def _run_in_directory(self, code: str, directory: Path, started: float) -> RunResult:
        script = directory / "generated.py"
        script.write_text(code, encoding="utf-8")
        try:
            process = subprocess.run(
                [sys.executable, str(script)],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=_safe_environment(self.environment_keys),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return RunResult(
                ok=False,
                returncode=124,
                stdout=_timeout_text(error.stdout),
                stderr=f"Execution exceeded {self.timeout_seconds:g} seconds",
                timed_out=True,
                duration_seconds=time.monotonic() - started,
            )

        return RunResult(
            ok=process.returncode == 0,
            returncode=process.returncode,
            stdout=process.stdout or "",
            stderr=process.stderr or "",
            timed_out=False,
            duration_seconds=time.monotonic() - started,
        )


def _safe_environment(environment_keys: Iterable[str] = ()) -> dict[str, str]:
    allowed = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    allowed.update(
        {key: os.environ[key] for key in environment_keys if key in os.environ}
    )
    allowed.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    return allowed


def _validate_environment_keys(environment_keys: Iterable[str]) -> tuple[str, ...]:
    keys = tuple(dict.fromkeys(key.strip() for key in environment_keys if key.strip()))
    invalid = [key for key in keys if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)]
    if invalid:
        raise ValueError(f"Invalid runtime environment variable names: {invalid}")
    return keys


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
