"""Tests for durable sessions and confirmed-contract snapshots."""

import json

import pytest

from doc2run_agent.storage.sessions import FileSessionStore
from doc2run_agent.schemas import InputSpec, OutputSpec


def test_session_round_trip(tmp_path):
    store = FileSessionStore(tmp_path)
    record = store.load_or_create("demo-session")
    record.draft_spec.objective = "Create a report"
    record.confirmed_sections.append("goal")
    store.save(record)

    restored = store.load_or_create("demo-session")

    assert restored.draft_spec.objective == "Create a report"
    assert restored.confirmed_sections == ["goal"]


def test_confirmed_spec_is_versioned(tmp_path):
    store = FileSessionStore(tmp_path)
    record = store.load_or_create("versioned")
    record.draft_spec.objective = "Create a report"
    record.draft_spec.inputs = [InputSpec(name="records", type="JSON", source="input.json")]
    record.draft_spec.outputs = [OutputSpec(name="report", format="CSV", destination="report.csv")]
    record.draft_spec.acceptance_criteria = ["CSV contains one row per category"]
    record.draft_spec.status = "ready_for_confirmation"
    record.draft_plan = "# 场景目标\n\nCreate a report.\n\n# 验收\n\n- CSV is valid"

    snapshot = store.snapshot_confirmed_spec(record)

    assert snapshot.version == 1
    saved = json.loads(
        (tmp_path / "versioned" / "task_specs" / "task_spec_v1.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "confirmed"
    assert (tmp_path / "versioned" / "planning" / "confirmed_plan.md").read_text(
        encoding="utf-8"
    ).strip() == record.confirmed_plan


def test_session_id_cannot_escape_base_directory(tmp_path):
    store = FileSessionStore(tmp_path)

    with pytest.raises(ValueError, match="session_id"):
        store.load_or_create("../outside")


def test_session_listing_excludes_archives(tmp_path):
    store = FileSessionStore(tmp_path)
    store.load_or_create("active")
    archived = tmp_path / "archives" / "hidden"
    archived.mkdir(parents=True)
    (archived / "session.json").write_text("{}", encoding="utf-8")

    assert store.list_session_ids() == ["active"]
