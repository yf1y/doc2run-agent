"""Tests for local multi-format knowledge loading and relevance ranking."""

import pytest

from doc2run_agent.knowledge.retriever import LocalKnowledgeBase
from doc2run_agent.knowledge.tools import KnowledgeSearchTool


def test_retriever_ranks_relevant_document_first(tmp_path):
    (tmp_path / "json.md").write_text("Use json.dumps to serialize dictionaries.", encoding="utf-8")
    (tmp_path / "csv.md").write_text("Use csv.DictReader to read table rows.", encoding="utf-8")

    knowledge = LocalKnowledgeBase.from_directory(tmp_path)
    result = knowledge.search("serialize a dictionary as JSON", top_k=1)

    assert result[0].source.startswith("json.md")
    assert result[0].score > 0


def test_retriever_reads_jsonl_entries(tmp_path):
    (tmp_path / "api.jsonl").write_text(
        '{"name":"read_text","doc":"Read UTF-8 text"}\n'
        '{"name":"write_text","doc":"Write UTF-8 text"}\n',
        encoding="utf-8",
    )

    knowledge = LocalKnowledgeBase.from_directory(tmp_path)

    assert "write_text" in knowledge.search("write text", top_k=1)[0].content


def test_retriever_reads_yaml_and_yml_as_knowledge(tmp_path):
    (tmp_path / "api.yaml").write_text("method: create_node\nreturns: node", encoding="utf-8")
    (tmp_path / "rules.yml").write_text("retry: exponential", encoding="utf-8")

    knowledge = LocalKnowledgeBase.from_directory(tmp_path)

    assert "create_node" in knowledge.search("create_node", top_k=1)[0].content
    assert "exponential" in knowledge.search("exponential retry", top_k=2)[0].content


def test_retriever_ignores_markdown_template_comments(tmp_path):
    (tmp_path / "placeholder.md").write_text(
        "<!-- Replace this comment with real documentation. -->", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Knowledge base is empty"):
        LocalKnowledgeBase.from_directory(tmp_path)


def test_retriever_can_mark_the_knowledge_source(tmp_path):
    (tmp_path / "reference.md").write_text("create_node(name)", encoding="utf-8")

    knowledge = LocalKnowledgeBase.from_directory(tmp_path, source_prefix="api:")

    assert knowledge.search("create_node", top_k=1)[0].source.startswith("api:")


def test_search_tool_refreshes_directory_backed_knowledge(tmp_path):
    document = tmp_path / "reference.md"
    document.write_text("old_api()", encoding="utf-8")
    tool = KnowledgeSearchTool(
        LocalKnowledgeBase.from_directory(tmp_path),
        source_directory=tmp_path,
    )

    document.write_text("new_api()", encoding="utf-8")
    tool.refresh()

    assert "new_api" in tool.search_many(["new_api"])[0]["content"]


def test_optional_search_tool_can_refresh_documents_added_later(tmp_path):
    source = tmp_path / "domain-docs"
    tool = KnowledgeSearchTool(
        None,
        source_directory=source,
        source_prefix="domain:power:",
        allow_empty=True,
    )

    tool.refresh()
    assert tool.search_many(["topology"]) == []

    source.mkdir()
    (source / "topology.md").write_text("The feeder topology is radial.", encoding="utf-8")
    tool.refresh()

    assert tool.has_knowledge is True
    assert tool.search_many(["radial topology"])[0]["source"].startswith("domain:power:")


def test_markdown_chunk_keeps_heading_and_code_block_together(tmp_path):
    (tmp_path / "sdk.md").write_text(
        "# Client\n\nCreate a client.\n\n"
        "## create_node\n\nUse this call:\n\n"
        "```python\nnode = create_node(name='n1')\n```\n",
        encoding="utf-8",
    )

    result = LocalKnowledgeBase.from_directory(tmp_path).search("create_node", top_k=1)[0]

    assert result.heading == "create_node"
    assert "```python" in result.content
    assert "node = create_node" in result.content
