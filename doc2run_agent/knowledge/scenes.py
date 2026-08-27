"""Persistence adapter for approved Scenario Plans in the Scene library."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..storage.files import atomic_text_write


class SceneLibrary:
    """Stores user-approved Scenario Plans directly as reusable Scene documents."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save_approved(self, scenario_plan: str, *, objective: str = "") -> Path:
        content = scenario_plan.strip() + "\n"
        if not content.strip():
            raise ValueError("Cannot approve an empty Scenario Plan")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        for path in self.directory.rglob("*.md"):
            if path.read_text(encoding="utf-8") == content:
                return path
        stem = _slug(objective) or "scene"
        path = self.directory / f"{stem}-{digest}.md"
        atomic_text_write(path, content)
        return path


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48].rstrip("-")
