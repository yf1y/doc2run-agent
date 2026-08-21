from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".txt", ".json", ".jsonl"}


@dataclass(frozen=True)
class KnowledgeChunk:
    source: str
    content: str


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    content: str
    score: float

    def to_dict(self) -> dict[str, object]:
        return {"source": self.source, "content": self.content, "score": self.score}


class LocalKnowledgeBase:
    def __init__(self, chunks: list[KnowledgeChunk], ngram_range: tuple[int, int] = (2, 4)) -> None:
        if not chunks:
            raise ValueError("Knowledge base is empty")
        self._chunks = chunks
        self._ngram_range = ngram_range
        self._document_terms = [Counter(self._terms(chunk.content)) for chunk in chunks]
        self._idf = self._build_idf(self._document_terms)
        self._vectors = [self._tfidf_vector(terms) for terms in self._document_terms]

    @classmethod
    def from_directory(cls, directory: str | Path) -> "LocalKnowledgeBase":
        root = Path(directory)
        if not root.is_dir():
            raise ValueError(f"Knowledge directory does not exist: {root}")

        chunks: list[KnowledgeChunk] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            for index, text in enumerate(_read_entries(path), start=1):
                for part_index, part in enumerate(_chunk_text(text), start=1):
                    source = f"{path.relative_to(root)}#{index}.{part_index}"
                    chunks.append(KnowledgeChunk(source=source, content=part))

        return cls(chunks)

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("Search query cannot be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")

        query_terms = Counter(self._terms(query))
        query_vector = self._tfidf_vector(query_terms)
        query_words = _words(query)
        ranked: list[RetrievedChunk] = []

        for chunk, vector in zip(self._chunks, self._vectors, strict=True):
            score = _cosine_similarity(query_vector, vector)
            chunk_words = _words(chunk.content)
            if query_words:
                score += 0.05 * len(query_words & chunk_words) / len(query_words)
            ranked.append(RetrievedChunk(chunk.source, chunk.content, score))

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[: min(top_k, len(ranked))]

    def _terms(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        minimum, maximum = self._ngram_range
        terms: list[str] = []
        for size in range(minimum, maximum + 1):
            terms.extend(normalized[index : index + size] for index in range(len(normalized) - size + 1))
        return terms

    @staticmethod
    def _build_idf(documents: list[Counter[str]]) -> dict[str, float]:
        document_count = len(documents)
        frequency: Counter[str] = Counter()
        for document in documents:
            frequency.update(document.keys())
        return {
            term: math.log((1 + document_count) / (1 + count)) + 1
            for term, count in frequency.items()
        }

    def _tfidf_vector(self, terms: Counter[str]) -> dict[str, float]:
        total = sum(terms.values()) or 1
        return {
            term: count / total * self._idf.get(term, 1.0)
            for term, count in terms.items()
        }


def _read_entries(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.dumps(json.loads(line), ensure_ascii=False, indent=2) for line in raw.splitlines() if line.strip()]
    if path.suffix.lower() == ".json":
        value = json.loads(raw)
        if isinstance(value, list):
            return [json.dumps(item, ensure_ascii=False, indent=2) for item in value]
        return [json.dumps(value, ensure_ascii=False, indent=2)]
    return [raw]


def _chunk_text(text: str, max_characters: int = 1400) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_characters:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                paragraph[index : index + max_characters]
                for index in range(0, len(paragraph), max_characters)
            )
            continue
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > max_characters:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())}


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(term, 0.0) for term, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)

