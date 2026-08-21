from doc2run_agent.errors import classify_failure
from doc2run_agent.schemas import CodeValidation, RunResult


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
