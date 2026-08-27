"""Memory stage for persisting only user-approved Scenario Plans as Scenes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..knowledge.scenes import SceneLibrary
from ..schemas import SessionRecord
from ..storage.sessions import FileSessionStore


@dataclass(frozen=True)
class MemoryResult:
    """Return the persisted session state and resulting Scene path."""

    record: SessionRecord
    scene_path: Path


class MemoryAgent:
    """Persist only a user-approved, successfully executed Scenario Plan."""

    def __init__(
        self,
        scene_library: SceneLibrary | None,
        store: FileSessionStore,
    ) -> None:
        self.scene_library = scene_library
        self.store = store

    def approve(self, record: SessionRecord, note: str = "") -> MemoryResult:
        if record.phase != "awaiting_review" or not record.run_result or not record.run_result.ok:
            raise ValueError("Only a successfully executed version awaiting review can be approved")
        if self.scene_library is None:
            raise ValueError("Scene library is not configured")
        if not record.confirmed_plan.strip():
            raise ValueError("The session has no confirmed Scenario Plan")

        objective = record.confirmed_spec.objective if record.confirmed_spec else ""
        path = self.scene_library.save_approved(record.confirmed_plan, objective=objective)
        record.approval_note = note.strip()
        record.approved_scene_path = str(path)
        record.phase = "memory"
        record.status = "memory"
        self.store.save(record)
        return MemoryResult(record=record, scene_path=path)
