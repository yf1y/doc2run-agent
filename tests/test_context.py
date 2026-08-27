"""Tests for model-context budgeting, trimming, and artifact recording."""

import json

import pytest

from doc2run_agent.storage.artifacts import ArtifactManager
from doc2run_agent.storage.sessions import FileSessionStore
from doc2run_agent.agents.context import (
    complete_and_record,
    estimate_tokens,
    merge_context,
    trim_run_result,
)
from doc2run_agent.llm import ModelSettings
from conftest import FakeModel


class LimitedFakeModel(FakeModel):
    """Fake model with a deliberately small context window for budget tests."""

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


def test_context_merge_keeps_different_documents_with_the_same_relative_name():
    api = {"source": "reference.md#1.1", "content": "create_node(name)"}
    domain = {"source": "reference.md#1.1", "content": "The layout has 33 nodes"}

    assert merge_context([api], [domain]) == [api, domain]


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


def test_api_context_artifact_keeps_all_selected_documents(tmp_path):
    manager = ArtifactManager(FileSessionStore(tmp_path))
    context = [
        {"source": "api:first", "content": "A" * 20_000, "heading": "First"},
        {"source": "api:second", "content": "second signature", "heading": "Second"},
    ]

    path = manager.save_api_context("demo", context)

    saved = path.read_text(encoding="utf-8")
    assert "api:first" in saved
    assert "api:second" in saved
    assert "second signature" in saved
