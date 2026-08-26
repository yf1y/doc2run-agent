import pytest

from doc2run_agent.schemas import TaskSpec
from doc2run_agent.validation import validate_code


def spec(**updates):
    return TaskSpec.model_validate(
        {
            "objective": "Create a report",
            "outputs": [{"name": "report", "format": "JSON", "destination": "report.json"}],
            "acceptance_criteria": ["The report exists"],
            **updates,
        }
    )


def test_validation_accepts_standard_library_and_relative_write():
    result = validate_code(
        "from pathlib import Path\nPath('report.json').write_text('{}')\n",
        spec(),
    )

    assert result.ok is True


def test_validation_rejects_unknown_dependency():
    result = validate_code("import pandas\n", spec())

    assert result.ok is False
    assert "pandas" in result.errors[0]


def test_validation_rejects_absolute_write_and_process_execution():
    result = validate_code(
        "import subprocess\nopen('/tmp/result.txt', 'w').write('x')\nsubprocess.run(['echo', 'x'])\n",
        spec(),
    )

    assert result.ok is False
    assert any("absolute path" in error for error in result.errors)
    assert any("subprocess.run" in error for error in result.errors)


def test_validation_rejects_windows_absolute_write_on_every_platform():
    result = validate_code(
        "from pathlib import Path\nPath('C:/temp/result.txt').write_text('x')\n",
        spec(),
    )

    assert result.ok is False
    assert any("absolute path" in error for error in result.errors)


@pytest.mark.parametrize(
    "code",
    [
        "import os as safe\nsafe.remove('output.txt')",
        "from os import remove as delete\ndelete('output.txt')",
        "import shutil\ndelete = shutil.rmtree\ndelete('output')",
        "from pathlib import Path as P\nP('output.txt').unlink()",
    ],
)
def test_validation_rejects_destructive_calls_through_simple_aliases(code):
    result = validate_code(code, spec())

    assert result.ok is False
    assert any("not allowed" in error for error in result.errors)


def test_validation_rejects_absolute_path_open_through_path_alias():
    result = validate_code(
        "from pathlib import Path as P\nP('/tmp/output.txt').open('w').write('x')",
        spec(),
    )

    assert result.ok is False
    assert any("absolute path" in error for error in result.errors)
