from __future__ import annotations

from typing import Any

from .retriever import LocalKnowledgeBase


class KnowledgeSearchTool:
    def __init__(
        self,
        knowledge_base: LocalKnowledgeBase,
        *,
        top_k: int = 5,
        max_context_characters: int = 10_000,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.top_k = top_k
        self.max_context_characters = max_context_characters

    def search_many(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        used_characters = 0
        for query in queries[:2]:
            for item in self.knowledge_base.search(query, top_k=self.top_k):
                if item.source in seen_sources:
                    continue
                if used_characters + len(item.content) > self.max_context_characters:
                    continue
                value = item.to_dict()
                value["query"] = query
                results.append(value)
                seen_sources.add(item.source)
                used_characters += len(item.content)
        return results
