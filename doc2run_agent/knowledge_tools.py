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
        seen_content: set[str] = set()
        used_characters = 0
        cleaned_queries = [query.strip() for query in queries[:4] if query.strip()]
        ranked_by_query = [
            (query, self.knowledge_base.search(query, top_k=self.top_k))
            for query in cleaned_queries
        ]

        # Interleave the result lists so the first query cannot consume the
        # complete context budget before later, more specific queries are seen.
        for rank in range(self.top_k):
            for query, ranked in ranked_by_query:
                if rank >= len(ranked):
                    continue
                item = ranked[rank]
                if item.source in seen_sources:
                    continue
                fingerprint = " ".join(item.content.lower().split())
                if fingerprint in seen_content:
                    continue
                if used_characters + len(item.content) > self.max_context_characters:
                    continue
                value = item.to_dict()
                value["query"] = query
                results.append(value)
                seen_sources.add(item.source)
                seen_content.add(fingerprint)
                used_characters += len(item.content)
        return results
