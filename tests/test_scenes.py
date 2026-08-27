"""Tests for Top-1 full-scene retrieval and approved-scene storage."""

from doc2run_agent.knowledge.scenes import SceneLibrary
from doc2run_agent.knowledge.tools import SceneSearchTool


def test_scene_search_ranks_documents_and_returns_one_full_document(tmp_path):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    five = "# 5 节点网络\n\n## 器件\n\n- 节点 1 到 5\n\n## 连接\n\n- 1-2-3-4-5\n"
    report = "# CSV 报表\n\n- 汇总记录并输出 CSV\n"
    (scenes / "five.md").write_text(five, encoding="utf-8")
    (scenes / "report.md").write_text(report, encoding="utf-8")

    selected = SceneSearchTool.from_directory(scenes).select("把 5 节点扩展到 33 节点")

    assert selected is not None
    assert selected["source"] == "scene:five.md"
    assert selected["content"] == five.strip()
    assert report not in selected["content"]


def test_scene_library_saves_confirmed_plan_directly_and_deduplicates(tmp_path):
    library = SceneLibrary(tmp_path / "scenes")
    plan = "# 33 节点场景\n\n## 排布与连接\n\n- 节点 1 到 33 顺序连接\n"

    first = library.save_approved(plan, objective="Create 33 node scene")
    second = library.save_approved(plan, objective="Create 33 node scene")

    assert first == second
    assert first.read_text(encoding="utf-8") == plan
    assert len(list((tmp_path / "scenes").glob("*.md"))) == 1
