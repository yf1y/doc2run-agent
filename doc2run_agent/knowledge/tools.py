"""Stage-facing search tools over API chunks and complete Scene documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .retriever import LocalKnowledgeBase


class KnowledgeSearchTool:
    """Retrieve a bounded, deduplicated API context for Code and Fix."""

    def __init__(
        self,
        knowledge_base: LocalKnowledgeBase | None,
        *,
        top_k: int = 5,
        max_context_characters: int = 10_000,
        source_directory: str | Path | None = None,
        source_prefix: str = "",
        allow_empty: bool = False,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.top_k = top_k
        self.max_context_characters = max_context_characters
        self.source_directory = Path(source_directory) if source_directory is not None else None
        self.source_prefix = source_prefix
        self.allow_empty = allow_empty

    @property
    def has_knowledge(self) -> bool:
        return self.knowledge_base is not None

    def refresh(self) -> None:
        """Reload a directory-backed index before a new generation attempt."""
        if self.source_directory is None:
            return
        try:
            self.knowledge_base = LocalKnowledgeBase.from_directory(
                self.source_directory,
                source_prefix=self.source_prefix,
            )
        except ValueError as error:
            if not self.allow_empty or (
                str(error) != "Knowledge base is empty"
                and not str(error).startswith("Knowledge directory does not exist:")
            ):
                raise
            self.knowledge_base = None

    def search_many(self, queries: list[str]) -> list[dict[str, Any]]:
        if self.knowledge_base is None:
            return []
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


class SceneSearchTool:
    """Select one whole Scene document for the Chat conversation."""

    def __init__(
        self,
        knowledge_base: LocalKnowledgeBase | None,
        *,
        source_directory: str | Path | None = None,
        source_prefix: str = "scene:",
    ) -> None:
        self.knowledge_base = knowledge_base
        self.source_directory = Path(source_directory) if source_directory is not None else None
        self.source_prefix = source_prefix

    @classmethod
    def from_directory(
        cls, directory: str | Path, *, source_prefix: str = "scene:"
    ) -> "SceneSearchTool":
        root = Path(directory)
        try:
            knowledge_base = LocalKnowledgeBase.from_document_directory(
                root, source_prefix=source_prefix
            )
        except ValueError as error:
            if str(error) != "Knowledge base is empty" and not str(error).startswith(
                "Knowledge directory does not exist:"
            ):
                raise
            knowledge_base = None
        return cls(knowledge_base, source_directory=root, source_prefix=source_prefix)

    @property
    def has_scenes(self) -> bool:
        return self.knowledge_base is not None

    def refresh(self) -> None:
        if self.source_directory is None:
            return
        refreshed = self.from_directory(
            self.source_directory, source_prefix=self.source_prefix
        )
        self.knowledge_base = refreshed.knowledge_base

    def select(self, query: str) -> dict[str, Any] | None:
        if self.knowledge_base is None or not query.strip():
            return None
        results = self.knowledge_base.search(query, top_k=1)
        return results[0].to_dict() if results else None
