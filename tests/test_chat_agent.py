"""Tests for Chat-stage requirement clarification and plan confirmation."""

import json

import pytest

from doc2run_agent.agents.chat import ChatAgent
from doc2run_agent.knowledge.tools import SceneSearchTool
from doc2run_agent.schemas import SessionRecord

from conftest import FakeModel, scenario_plan_text


def test_chat_agent_collects_at_most_two_questions():
    response = json.dumps(
        {
            "spec_patch": {"objective": "Create a CSV report"},
            "confirmed_sections": ["goal"],
            "questions": ["Input?", "Output path?", "Extra question?"],
            "assistant_message": "I understand the goal.",
            "scenario_plan": "",
        }
    )
    record = ChatAgent(FakeModel([response])).process(
        SessionRecord(session_id="demo"),
        "Create a CSV report",
    )

    assert record.phase == "collecting_inputs_outputs"
    assert record.pending_questions == ["Input?", "Output path?"]
    assert record.confirmed_sections == ["goal"]


def test_chat_agent_reaches_confirmation_only_when_complete():
    response = json.dumps(
        {
            "spec_patch": {
                "objective": "Create a CSV report",
                "inputs": [{"name": "records", "type": "JSON", "source": "input.json"}],
                "outputs": [{"name": "report", "format": "CSV", "destination": "report.csv"}],
                "constraints": ["Use the documented SDK"],
                "allowed_apis": ["records_api"],
                "side_effects": ["Write report.csv"],
                "acceptance_criteria": ["The CSV contains a header and one row per category"],
            },
            "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
            "questions": [],
            "assistant_message": "The specification is ready.",
            "scenario_plan": scenario_plan_text(),
        }
    )
    record = ChatAgent(FakeModel([response])).process(
        SessionRecord(session_id="demo"),
        "Here are all details",
    )

    assert record.phase == "awaiting_confirmation"
    assert record.draft_spec.status == "ready_for_confirmation"
    assert record.pending_questions == []
    assert record.draft_plan == scenario_plan_text().strip()


def test_chat_agent_rejects_unknown_spec_fields():
    response = json.dumps(
        {
            "spec_patch": {"objective": "Do work", "secret_path": "/tmp"},
            "confirmed_sections": ["goal"],
            "questions": [],
            "assistant_message": "",
            "scenario_plan": "",
        }
    )

    with pytest.raises(ValueError, match="unknown TaskSpec fields"):
        ChatAgent(FakeModel([response])).process(
            SessionRecord(session_id="demo"),
            "Do work",
        )


def test_chat_agent_keeps_explicit_user_decisions():
    response = json.dumps(
        {
            "spec_patch": {"objective": "Create a network"},
            "confirmed_sections": ["goal"],
            "questions": ["Which input file should be used?"],
            "assistant_message": "The goal is clear.",
            "decisions": ["Layout means connectivity, not drawing coordinates."],
            "scenario_plan": "",
        }
    )

    record = ChatAgent(FakeModel([response])).process(
        SessionRecord(session_id="demo"),
        "By layout I mean connectivity, not drawing coordinates.",
    )

    assert record.decisions == ["Layout means connectivity, not drawing coordinates."]


def test_chat_agent_selects_one_scene_and_injects_its_full_document(tmp_path):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    five_node = "# 5 节点场景\n\n## 器件\n\n- 5 个节点\n\n## 连接\n\n- 1-2-3-4-5\n"
    unrelated = "# 报表场景\n\n- 汇总 CSV 数据\n"
    (scenes / "five.md").write_text(five_node, encoding="utf-8")
    (scenes / "report.md").write_text(unrelated, encoding="utf-8")
    response = json.dumps(
        {
            "spec_patch": {"objective": "生成 33 节点网络"},
            "confirmed_sections": ["goal"],
            "questions": ["输入参数是什么？"],
            "assistant_message": "已参考最相关场景。",
            "scenario_plan": "# 场景目标\n\n生成 33 节点网络。",
        }
    )
    model = FakeModel([response])

    record = ChatAgent(
        model, SceneSearchTool.from_directory(scenes)
    ).process(SessionRecord(session_id="scene"), "把 5 节点结构泛化为 33 节点")

    assert record.selected_scene is not None
    assert record.selected_scene["source"] == "scene:five.md"
    assert record.selected_scene["content"] == five_node.strip()
    assert five_node.strip() in model.calls[0][1]
    assert unrelated not in model.calls[0][1]


def test_chat_agent_requires_structured_plan_before_confirmation():
    response = json.dumps(
        {
            "spec_patch": {
                "objective": "Create a report",
                "outputs": [{"name": "report", "format": "CSV", "destination": "report.csv"}],
                "acceptance_criteria": ["report.csv exists"],
            },
            "confirmed_sections": ["goal", "inputs_outputs", "constraints", "acceptance"],
            "questions": [],
            "assistant_message": "需求已齐全。",
            "scenario_plan": "not structured",
        }
    )

    record = ChatAgent(FakeModel([response])).process(
        SessionRecord(session_id="plan"), "生成报表"
    )

    assert record.phase == "collecting_plan"
    assert record.draft_spec.status == "draft"
