"""Tests for persisting only approved Scenario Plans as scene knowledge."""

from doc2run_agent.agents.memory import MemoryAgent
from doc2run_agent.knowledge.scenes import SceneLibrary
from doc2run_agent.schemas import RunResult
from doc2run_agent.storage.sessions import FileSessionStore


def test_memory_persists_only_the_approved_confirmed_plan(tmp_path):
    store = FileSessionStore(tmp_path / "sessions")
    record = store.load_or_create("memory")
    record.phase = "awaiting_review"
    record.confirmed_plan = "# 场景目标\n\n生成 5 节点网络。"
    record.run_result = RunResult(
        ok=True,
        returncode=0,
        stdout="{}\n",
        stderr="",
        timed_out=False,
        duration_seconds=0.1,
    )
    store.save(record)

    result = MemoryAgent(SceneLibrary(tmp_path / "scenes"), store).approve(record, "verified")

    assert result.record.phase == "memory"
    assert result.record.status == "memory"
    assert result.record.approval_note == "verified"
    assert result.scene_path.read_text(encoding="utf-8") == record.confirmed_plan + "\n"
    assert not (tmp_path / "memory").exists()
