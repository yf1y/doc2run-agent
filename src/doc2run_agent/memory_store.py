from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .retriever import KnowledgeChunk, LocalKnowledgeBase
from .schemas import MemoryReview, ScenarioCandidate


DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CORE_FORBIDDEN_KEYS = {
    "api", "apis", "signature", "signatures", "import", "imports", "code",
    "source_code", "function", "functions", "method", "methods", "token", "credentials",
}


class DomainMemorySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    scenario_kind: str
    required_fields: list[str]
    allowed_fields: list[str]
    field_types: dict[str, str] = Field(default_factory=dict)
    forbidden_keys: list[str] = Field(default_factory=list)


class ScenarioMemoryStore:
    def __init__(self, base_directory: str | Path, domain_schemas: str | Path) -> None:
        self.base_directory = Path(base_directory)
        self.domain_schemas = Path(domain_schemas)
        for name in ("candidates", "approved", "rejected"):
            (self.base_directory / name).mkdir(parents=True, exist_ok=True)

    def load_schema(self, domain: str) -> DomainMemorySchema:
        self._validate_domain(domain)
        path = self.domain_schemas / domain / "memory_schema.json"
        if not path.is_file():
            raise ValueError(f"No memory schema is configured for domain '{domain}': {path}")
        return DomainMemorySchema.model_validate_json(path.read_text(encoding="utf-8"))

    def validate_candidate(
        self, candidate: ScenarioCandidate, schema: DomainMemorySchema
    ) -> list[str]:
        errors: list[str] = []
        if candidate.scenario_kind != schema.scenario_kind:
            errors.append(
                f"scenario_kind must be '{schema.scenario_kind}', got '{candidate.scenario_kind}'"
            )
        keys = set(candidate.data)
        missing = set(schema.required_fields) - keys
        extra = keys - set(schema.allowed_fields)
        if missing:
            errors.append(f"missing required data fields: {sorted(missing)}")
        if extra:
            errors.append(f"data fields are not allowed by the domain schema: {sorted(extra)}")
        forbidden = {item.lower() for item in schema.forbidden_keys} | CORE_FORBIDDEN_KEYS
        found = sorted(_find_forbidden_keys(candidate.data, forbidden))
        if found:
            errors.append(f"API/code-like fields are forbidden in scenario memory: {found}")
        for field, expected in schema.field_types.items():
            if field in candidate.data and not _matches_json_type(candidate.data[field], expected):
                errors.append(f"data.{field} must be JSON type '{expected}'")
        return errors

    def save_candidate(
        self,
        *,
        session_id: str,
        domain: str,
        candidate: ScenarioCandidate,
        validation_errors: list[str],
        review: MemoryReview,
        approval_note: str,
    ) -> tuple[str, Path]:
        self._validate_domain(domain)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        candidate_id = f"{session_id}_{stamp}"
        directory = self.base_directory / "candidates" / domain / candidate_id
        payload = {
            "manifest": {
                "schema_version": "1",
                "knowledge_type": "scenario",
                "domain": domain,
                "scenario_kind": candidate.scenario_kind,
                "scenario_name": candidate.scenario_name,
                "source_session": session_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "approval_note": approval_note,
            },
            "candidate": candidate.model_dump(mode="json"),
            "validation_errors": validation_errors,
            "review": review.model_dump(mode="json"),
        }
        path = directory / "scenario.json"
        _atomic_json_write(path, payload)
        return candidate_id, path

    def approve(self, domain: str, candidate_id: str) -> Path:
        source = self._candidate_directory(domain, candidate_id)
        payload = self._load_candidate(source)
        if payload.get("validation_errors") or not payload.get("review", {}).get("ok"):
            raise ValueError("This scenario candidate did not pass validation and review")
        target = self.base_directory / "approved" / domain / candidate_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("This scenario candidate is already approved")
        os.replace(source, target)
        payload["manifest"]["approved_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json_write(target / "scenario.json", payload)
        return target / "scenario.json"

    def reject(self, domain: str, candidate_id: str) -> Path:
        source = self._candidate_directory(domain, candidate_id)
        target = self.base_directory / "rejected" / domain / candidate_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("This scenario candidate is already rejected")
        os.replace(source, target)
        return target / "scenario.json"

    def search(self, domain: str, query: str, top_k: int = 2) -> list[dict[str, Any]]:
        if not domain:
            return []
        self._validate_domain(domain)
        root = self.base_directory / "approved" / domain
        chunks: list[KnowledgeChunk] = []
        if not root.is_dir():
            return []
        for path in sorted(root.glob("*/scenario.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            candidate = payload.get("candidate", {})
            content = json.dumps(
                {
                    "scenario_kind": candidate.get("scenario_kind", ""),
                    "scenario_name": candidate.get("scenario_name", ""),
                    "summary": candidate.get("summary", ""),
                    "data": candidate.get("data", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
            chunks.append(
                KnowledgeChunk(
                    source=f"approved-scenario:{path.parent.name}",
                    content=content,
                    heading=str(candidate.get("scenario_name", "")),
                    kind="scenario",
                )
            )
        if not chunks or not query.strip():
            return []
        return [item.to_dict() for item in LocalKnowledgeBase(chunks).search(query, top_k=top_k)]

    def _candidate_directory(self, domain: str, candidate_id: str) -> Path:
        self._validate_domain(domain)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate_id):
            raise ValueError("Invalid scenario candidate ID")
        source = self.base_directory / "candidates" / domain / candidate_id
        if not source.is_dir():
            raise ValueError("Scenario candidate does not exist or is no longer pending")
        return source

    @staticmethod
    def _load_candidate(directory: Path) -> dict[str, Any]:
        return json.loads((directory / "scenario.json").read_text(encoding="utf-8"))

    @staticmethod
    def _validate_domain(domain: str) -> None:
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError("domain must contain 1-64 letters, numbers, underscores, or hyphens")


def _find_forbidden_keys(value: Any, forbidden: set[str], prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in forbidden:
                found.add(path)
            found.update(_find_forbidden_keys(nested, forbidden, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.update(_find_forbidden_keys(nested, forbidden, f"{prefix}[{index}]"))
    return found


def _matches_json_type(value: Any, expected: str) -> bool:
    return {
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }.get(expected, lambda _item: False)(value)


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with temporary.open("w", encoding="utf-8") as file_object:
            json.dump(value, file_object, ensure_ascii=False, indent=2)
            file_object.write("\n")
            file_object.flush()
            os.fsync(file_object.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
