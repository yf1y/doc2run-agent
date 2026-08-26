import json

import pytest

from doc2run_agent.artifacts import ArtifactManager
from doc2run_agent.context import complete_and_record, estimate_tokens, trim_run_result
from doc2run_agent.llm import ModelSettings
from doc2run_agent.session_store import FileSessionStore

from conftest import FakeModel


class LimitedFakeModel(FakeModel):
    settings = ModelSettings(model="fake", context_tokens=1000, max_tokens=100)


def test_token_estimate_counts_cjk_more_conservatively_than_ascii():
    assert estimate_tokens("电" * 20) > estimate_tokens("a" * 20)


def test_model_call_rejects_input_over_configured_budget():
    model = LimitedFakeModel(["unused"])

    with pytest.raises(ValueError, match="after reserving 100 output tokens"):
        complete_and_record(
            model,
            stage="test",
            system_prompt="system",
            user_prompt="电" * 1100,
        )


def test_run_output_is_trimmed_before_fix_prompt():
    trimmed = trim_run_result({"stdout": "x" * 4000, "stderr": "y" * 8000})

    assert "omitted" in trimmed["stdout"]
    assert "omitted" in trimmed["stderr"]
    assert len(trimmed["stdout"]) < 2200
    assert len(trimmed["stderr"]) < 6200


def test_context_artifacts_append_without_duplicating_calls(tmp_path):
    store = FileSessionStore(tmp_path)
    manager = ArtifactManager(store)
    first = {
        "stage": "first",
        "system_prompt": "s1",
        "user_prompt": "u1",
        "response": "r1",
        "estimated_tokens": 2,
        "sources": [],
    }
    second = {**first, "stage": "second", "user_prompt": "u2"}

    manager.save_context_records("demo", [first])
    manager.save_context_records("demo", [first, second])

    manifest = json.loads(
        (tmp_path / "demo" / "contexts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [item["stage"] for item in manifest] == ["first", "second"]
    assert len(list((tmp_path / "demo" / "contexts").glob("*.md"))) == 2
