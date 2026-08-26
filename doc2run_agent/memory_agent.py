from __future__ import annotations

from typing import Any

from .context import complete_and_record
from .llm import TextModel
from .memory_store import ScenarioMemoryStore
from .parsing import parse_model
from .prompts import (
    MEMORY_EXTRACT_SYSTEM,
    MEMORY_REVIEW_SYSTEM,
    memory_extract_request,
    memory_review_request,
)
from .schemas import MemoryReview, ScenarioCandidate


class MemoryAgent:
    """Creates one reviewed scenario candidate in two deliberately isolated model calls."""

    def __init__(self, model: TextModel, store: ScenarioMemoryStore) -> None:
        self.model = model
        self.store = store

    def create_candidate(
        self,
        *,
        session_id: str,
        domain: str,
        task_spec: dict[str, Any],
        implementation_plan: dict[str, Any],
        code: str,
        run_result: dict[str, Any],
        approval_note: str,
    ) -> dict[str, Any]:
        schema = self.store.load_schema(domain)
        extraction_prompt = memory_extract_request(
            domain=domain,
            schema=schema.model_dump(mode="json"),
            task_spec=task_spec,
            implementation_plan=implementation_plan,
            code=code,
            run_result=run_result,
            approval_note=approval_note,
        )
        extraction, extraction_records = complete_and_record(
            self.model,
            stage="memory_extract",
            system_prompt=MEMORY_EXTRACT_SYSTEM,
            user_prompt=extraction_prompt,
            current=[],
        )
        candidate = parse_model(extraction, ScenarioCandidate)
        validation_errors = self.store.validate_candidate(candidate, schema)

        review_prompt = memory_review_request(
            schema=schema.model_dump(mode="json"),
            candidate=candidate.model_dump(mode="json"),
            validation_errors=validation_errors,
            task_spec=task_spec,
            implementation_plan=implementation_plan,
            code=code,
            run_result=run_result,
        )
        review_text, review_records = complete_and_record(
            self.model,
            stage="memory_review",
            system_prompt=MEMORY_REVIEW_SYSTEM,
            user_prompt=review_prompt,
            current=[],
        )
        review = parse_model(review_text, MemoryReview)
        if validation_errors and review.ok:
            review = review.model_copy(
                update={
                    "ok": False,
                    "problems": list(dict.fromkeys(review.problems + validation_errors)),
                }
            )
        candidate_id, path = self.store.save_candidate(
            session_id=session_id,
            domain=domain,
            candidate=candidate,
            validation_errors=validation_errors,
            review=review,
            approval_note=approval_note,
        )
        return {
            "candidate_id": candidate_id,
            "candidate": candidate.model_dump(mode="json"),
            "validation_errors": validation_errors,
            "review": review.model_dump(mode="json"),
            "path": str(path),
            "context_records": extraction_records + review_records,
        }
