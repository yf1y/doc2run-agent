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
