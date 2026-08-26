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
    heading: str = ""
    kind: str = "text"


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    content: str
    score: float
    heading: str = ""
    kind: str = "text"

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "content": self.content,
            "score": self.score,
            "heading": self.heading,
            "kind": self.kind,
        }


class LocalKnowledgeBase:
    def __init__(self, chunks: list[KnowledgeChunk], ngram_range: tuple[int, int] = (2, 4)) -> None:
        if not chunks:
            raise ValueError("Knowledge base is empty")
        self._chunks = chunks
        self._ngram_range = ngram_range
        self._document_terms = [Counter(self._terms(chunk.content)) for chunk in chunks]
        self._idf = self._build_idf(self._document_terms)
        self._vectors = [self._tfidf_vector(terms) for terms in self._document_terms]
        self._word_terms = [Counter(_word_list(chunk.content)) for chunk in chunks]
        self._word_document_frequency = self._build_word_document_frequency(self._word_terms)
        self._average_word_count = (
            sum(sum(terms.values()) for terms in self._word_terms) / len(self._word_terms)
        )

    @classmethod
    def from_directory(
        cls, directory: str | Path, *, source_prefix: str = ""
    ) -> "LocalKnowledgeBase":
        root = Path(directory)
        if not root.is_dir():
            raise ValueError(f"Knowledge directory does not exist: {root}")

        chunks: list[KnowledgeChunk] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            for index, text in enumerate(_read_entries(path), start=1):
                for part_index, (part, heading, kind) in enumerate(
                    _chunk_document(text, path.suffix.lower()), start=1
                ):
                    source = f"{source_prefix}{path.relative_to(root)}#{index}.{part_index}"
                    chunks.append(
                        KnowledgeChunk(source=source, content=part, heading=heading, kind=kind)
                    )

        return cls(chunks)

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("Search query cannot be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")

        query_terms = Counter(self._terms(query))
        query_vector = self._tfidf_vector(query_terms)
        query_words = _words(query)
        query_identifiers = _identifiers(query)
        raw_scores: list[tuple[KnowledgeChunk, float, float, float]] = []

        for chunk, vector, word_terms in zip(
            self._chunks, self._vectors, self._word_terms, strict=True
        ):
            character_score = _cosine_similarity(query_vector, vector)
            chunk_words = _words(chunk.content)
            overlap = len(query_words & chunk_words) / len(query_words) if query_words else 0.0
            bm25 = self._bm25(query_words, word_terms)
            identifiers = _identifiers(chunk.content)
            identifier_overlap = (
                len(query_identifiers & identifiers) / len(query_identifiers)
                if query_identifiers
                else 0.0
            )
            if chunk.heading:
                heading_words = _words(chunk.heading)
                overlap = max(overlap, len(query_words & heading_words) / len(query_words) if query_words else 0.0)
            raw_scores.append((chunk, character_score, bm25, overlap + identifier_overlap))

        maximum_bm25 = max((item[2] for item in raw_scores), default=0.0) or 1.0
        ranked = [
            RetrievedChunk(
                chunk.source,
                chunk.content,
                0.35 * character_score + 0.45 * (bm25 / maximum_bm25) + 0.20 * overlap,
                chunk.heading,
                chunk.kind,
            )
            for chunk, character_score, bm25, overlap in raw_scores
        ]

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[: min(top_k, len(ranked))]

    def _bm25(self, query_words: set[str], document: Counter[str]) -> float:
        if not query_words or not document:
            return 0.0
        document_count = len(self._word_terms)
        document_length = sum(document.values())
        k1 = 1.5
        b = 0.75
        score = 0.0
        for word in query_words:
            frequency = document.get(word, 0)
            if not frequency:
                continue
            document_frequency = self._word_document_frequency.get(word, 0)
            idf = math.log(1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
            denominator = frequency + k1 * (
                1 - b + b * document_length / (self._average_word_count or 1.0)
            )
            score += idf * frequency * (k1 + 1) / denominator
        return score

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

    @staticmethod
    def _build_word_document_frequency(documents: list[Counter[str]]) -> Counter[str]:
        frequency: Counter[str] = Counter()
        for document in documents:
            frequency.update(document.keys())
        return frequency

    def _tfidf_vector(self, terms: Counter[str]) -> dict[str, float]:
        total = sum(terms.values()) or 1
        return {
            term: count / total * self._idf.get(term, 1.0)
            for term, count in terms.items()
        }


def _read_entries(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8").strip()
    if path.suffix.lower() == ".md":
        raw = re.sub(r"<!--[\s\S]*?-->", "", raw).strip()
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


def _chunk_document(text: str, suffix: str) -> list[tuple[str, str, str]]:
    if suffix == ".md":
        return _chunk_markdown(text)
    return [(part, "", "data" if suffix in {".json", ".jsonl"} else "text") for part in _chunk_text(text)]


def _chunk_markdown(text: str) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, list[str]]] = []
    heading = ""
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and re.match(r"^#{1,6}\s+", line):
            if lines:
                sections.append((heading, lines))
            heading = re.sub(r"^#{1,6}\s+", "", line).strip()
            lines = [line]
        else:
            lines.append(line)
    if lines:
        sections.append((heading, lines))

    chunks: list[tuple[str, str, str]] = []
    for section_heading, section_lines in sections:
        section = "\n".join(section_lines).strip()
        kind = "code" if "```" in section else "text"
        chunks.extend((part, section_heading, kind) for part in _chunk_text(section))
    return chunks


def _chunk_text(text: str, max_characters: int = 1800) -> list[str]:
    blocks = [
        match.group(0).strip()
        for match in re.finditer(r"```[\s\S]*?```|(?:[^\n]|\n(?!\s*\n))+", text)
        if match.group(0).strip()
    ]
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > max_characters and not block.startswith("```"):
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                block[index : index + max_characters]
                for index in range(0, len(block), max_characters)
            )
            continue
        candidate = f"{current}\n\n{block}".strip()
        if current and len(candidate) > max_characters:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _words(text: str) -> set[str]:
    return set(_word_list(text))


def _word_list(text: str) -> list[str]:
    latin = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return latin + chinese


def _identifiers(text: str) -> set[str]:
    return {
        value.lower()
        for value in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b", text)
        if "_" in value or "." in value or any(character.isupper() for character in value)
    }


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(term, 0.0) for term, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
