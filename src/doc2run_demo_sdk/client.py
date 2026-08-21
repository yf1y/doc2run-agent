from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RecordNotFoundError(LookupError):
    """Raised when a requested record does not exist."""


class RecordClient:
    """Small file-backed record service with an SDK-shaped interface."""

    def __init__(self, database_path: str | Path = "demo_records.json") -> None:
        self.database_path = Path(database_path)

    def list_records(self, *, status: str | None = None) -> list[dict[str, Any]]:
        records = self._read()
        if status is not None:
            records = [record for record in records if record.get("status") == status]
        return [dict(record) for record in records]

    def get_record(self, record_id: str) -> dict[str, Any]:
        for record in self._read():
            if record.get("id") == record_id:
                return dict(record)
        raise RecordNotFoundError(record_id)

    def create_record(self, *, title: str, status: str = "open") -> dict[str, Any]:
        records = self._read()
        numeric_ids = [int(item["id"]) for item in records if str(item.get("id", "")).isdigit()]
        record = {"id": str(max(numeric_ids, default=0) + 1), "title": title, "status": status}
        records.append(record)
        self._write(records)
        return dict(record)

    def _read(self) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return [
                {"id": "1", "title": "Prepare weekly report", "status": "open"},
                {"id": "2", "title": "Archive completed run", "status": "done"},
            ]
        value = json.loads(self.database_path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("Record database must contain a JSON array of objects")
        return value

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
