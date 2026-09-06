"""Tests for normalized validation and runtime error classification."""

from doc2run_agent.runtime.errors import classify_failure, is_environment_failure
from doc2run_agent.schemas import CodeValidation, ErrorInfo, RunResult, TaskSpec


def test_error_classifier_detects_missing_dependency():
    result = RunResult(
        ok=False,
        returncode=1,
        stdout="",
        stderr="Traceback...\nModuleNotFoundError: No module named 'example'",
        timed_out=False,
        duration_seconds=0.1,
    )

    info = classify_failure(result)

    assert info.category == "missing_dependency"
    assert info.exception_type == "ModuleNotFoundError"


def test_static_validation_has_priority_over_execution():
    validation = CodeValidation(ok=False, errors=["Import is not allowed"])

    info = classify_failure(None, validation)

    assert info.category == "static_validation"


def test_only_declared_missing_input_is_an_environment_blocker(tmp_path):
    path = str(tmp_path / "supplied-input.csv")
    info = ErrorInfo(category="missing_input", exception_type="FileNotFoundError", message=f"[Errno 2]: {path!r}")
    assert not is_environment_failure(info, TaskSpec())
    spec = TaskSpec(inputs=[{"name": "data", "type": "file", "source": path}])
    assert is_environment_failure(info, spec)
    (tmp_path / "supplied-input.csv").write_text("value\n1\n", encoding="utf-8")
    assert not is_environment_failure(info, spec)


def test_wrong_submodule_of_installed_package_can_be_fixed():
    info = ErrorInfo(category="missing_dependency", exception_type="ModuleNotFoundError",
                     message="No module named 'json.not_a_real_submodule'")
    assert not is_environment_failure(info, TaskSpec())
