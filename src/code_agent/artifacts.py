from __future__ import annotations

from pathlib import Path
from typing import Any

from .session_store import FileSessionStore


class ArtifactManager:
    def __init__(self, store: FileSessionStore) -> None:
        self.store = store

    def workspace(self, session_id: str) -> Path:
        directory = self.store.session_directory(session_id) / "workspace"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_retrieval(
        self,
        session_id: str,
        *,
        stage: str,
        round_index: int,
        queries: list[str],
        context: list[dict[str, Any]],
    ) -> Path:
        return self.store.write_json(
            session_id,
            Path("retrieval") / f"{stage}_round_{round_index:03d}.json",
            {"stage": stage, "round": round_index, "queries": queries, "results": context},
        )

    def save_generation(
        self,
        session_id: str,
        *,
        attempt: int,
        code: str,
        validation: dict[str, Any],
    ) -> list[Path]:
        directory = self._run_directory(attempt)
        return [
            self.store.write_text(session_id, directory / "generated.py", code),
            self.store.write_json(session_id, directory / "validation.json", validation),
        ]

    def save_execution(
        self,
        session_id: str,
        *,
        attempt: int,
        run_result: dict[str, Any],
    ) -> list[Path]:
        directory = self._run_directory(attempt)
        return [
            self.store.write_text(session_id, directory / "stdout.txt", str(run_result["stdout"])),
            self.store.write_text(session_id, directory / "stderr.txt", str(run_result["stderr"])),
            self.store.write_json(session_id, directory / "run.json", run_result),
        ]

    @staticmethod
    def _run_directory(attempt: int) -> Path:
        return Path("runs") / ("initial" if attempt == 0 else f"fix_{attempt:03d}")
