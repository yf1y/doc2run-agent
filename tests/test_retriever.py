import pytest

from doc2run_agent.retriever import LocalKnowledgeBase


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
