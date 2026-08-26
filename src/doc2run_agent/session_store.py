from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_utils import atomic_json_write, atomic_text_write
from .schemas import SessionRecord, TaskSpec, utc_now


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class FileSessionStore:
    def __init__(self, base_directory: str | Path = "sessions") -> None:
        self.base_directory = Path(base_directory)
        self.base_directory.mkdir(parents=True, exist_ok=True)

    def load_or_create(self, session_id: str) -> SessionRecord:
        path = self._session_file(session_id)
        if path.exists():
            return SessionRecord.model_validate_json(path.read_text(encoding="utf-8"))
        record = SessionRecord(session_id=session_id)
        self.save(record)
        return record

    def save(self, record: SessionRecord) -> None:
        self._validate_session_id(record.session_id)
        record.updated_at = utc_now()
        atomic_json_write(
            self._session_file(record.session_id),
            record.model_dump(mode="json"),
        )

    def snapshot_confirmed_spec(self, record: SessionRecord) -> TaskSpec:
        if record.draft_spec.status != "ready_for_confirmation":
            raise ValueError("Task specification is not ready for confirmation")
        task_spec_directory = self.session_directory(record.session_id) / "task_specs"
        task_spec_directory.mkdir(parents=True, exist_ok=True)
        versions = [
            int(match.group(1))
            for path in task_spec_directory.glob("task_spec_v*.json")
            if (match := re.fullmatch(r"task_spec_v(\d+)\.json", path.name))
        ]
        version = max(versions, default=0) + 1
        snapshot = record.draft_spec.model_copy(
            deep=True,
            update={"status": "confirmed", "version": version},
        )
        atomic_json_write(
            task_spec_directory / f"task_spec_v{version}.json",
            snapshot.model_dump(mode="json"),
        )
        record.confirmed_spec = snapshot
        record.draft_spec = snapshot.model_copy(deep=True)
        record.status = "confirmed"
        self.save(record)
        return snapshot

    def reset(self, session_id: str) -> SessionRecord:
        directory = self.session_directory(session_id)
        if directory.exists():
            archive_directory = self.base_directory / "archives"
            archive_directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            os.replace(directory, archive_directory / f"{session_id}_{timestamp}")
        record = SessionRecord(session_id=session_id)
        self.save(record)
        return record

    def session_directory(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        return self.base_directory / session_id

    def write_json(self, session_id: str, relative_path: str | Path, value: Any) -> Path:
        path = self.session_directory(session_id) / relative_path
        atomic_json_write(path, value)
        return path

    def write_text(self, session_id: str, relative_path: str | Path, value: str) -> Path:
        path = self.session_directory(session_id) / relative_path
        atomic_text_write(path, value)
        return path

    def _session_file(self, session_id: str) -> Path:
        return self.session_directory(session_id) / "session.json"

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(
                "session_id must contain 1-64 letters, numbers, underscores, or hyphens"
            )
