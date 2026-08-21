from doc2run_agent.runner import LocalPythonRunner, sanitize_code


def test_runner_executes_code_and_captures_stdout():
    result = LocalPythonRunner(timeout_seconds=1).run("print('hello')")

    assert result.ok is True
    assert result.stdout.strip() == "hello"


def test_runner_reports_python_failure():
    result = LocalPythonRunner(timeout_seconds=1).run("raise RuntimeError('broken')")

    assert result.ok is False
    assert result.returncode != 0
    assert "RuntimeError: broken" in result.stderr


def test_runner_stops_timed_out_code():
    result = LocalPythonRunner(timeout_seconds=0.05).run("import time\ntime.sleep(2)")

    assert result.ok is False
    assert result.timed_out is True
    assert result.returncode == 124


def test_runner_does_not_pass_model_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "another-secret")

    result = LocalPythonRunner(timeout_seconds=1).run(
        "import os\nprint(os.getenv('OPENAI_API_KEY'), os.getenv('ANTHROPIC_API_KEY'))"
    )

    assert result.ok is True
    assert result.stdout.strip() == "None None"


def test_sanitize_code_removes_markdown_fence():
    assert sanitize_code("```python\nprint('ok')\n```") == "print('ok')\n"
