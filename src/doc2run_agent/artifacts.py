from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .context import context_manifest, context_markdown
from .prompts import format_context
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

    def save_decisions(self, session_id: str, decisions: list[str]) -> Path:
        content = "# User decisions\n"
        if decisions:
            content += "\n" + "\n".join(f"- {decision}" for decision in decisions) + "\n"
        else:
            content += "\nNo explicit decisions have been recorded.\n"
        return self.store.write_text(session_id, "decisions.md", content)

    def save_planning(
        self,
        session_id: str,
        *,
        initial_context: list[dict[str, Any]],
        additional_context: list[dict[str, Any]],
        scenario_context: list[dict[str, Any]],
        initial_implementation_plan: dict[str, Any],
        implementation_plan: dict[str, Any],
        initial_plan_review: dict[str, Any],
        plan_review: dict[str, Any],
    ) -> list[Path]:
        combined = _merge_context(initial_context, additional_context)
        return [
            self.store.write_text(
                session_id,
                Path("planning") / "api_context.md",
                "# Selected documentation\n\n" + format_context(combined) + "\n",
            ),
            self.store.write_text(
                session_id,
                Path("planning") / "scenario_context.md",
                "# Approved same-domain scenario examples\n\n"
                + format_context(scenario_context)
                + "\n",
            ),
            self.store.write_json(
                session_id,
                Path("planning") / "implementation_plan_initial.json",
                initial_implementation_plan,
            ),
            self.store.write_json(
                session_id, Path("planning") / "implementation_plan.json", implementation_plan
            ),
            self.store.write_json(
                session_id, Path("planning") / "plan_review_initial.json", initial_plan_review
            ),
            self.store.write_json(
                session_id, Path("planning") / "plan_review.json", plan_review
            ),
            self.store.write_text(
                session_id,
                Path("planning") / "generation_notes.md",
                _generation_notes(implementation_plan),
            ),
        ]

    def save_fix_details(
        self,
        session_id: str,
        *,
        attempt: int,
        fix_plan: dict[str, Any],
        code_patch: dict[str, Any],
        patch_review: dict[str, Any],
        patch_error: str,
    ) -> list[Path]:
        directory = self._run_directory(attempt)
        return [
            self.store.write_json(session_id, directory / "fix_plan.json", fix_plan),
            self.store.write_json(session_id, directory / "code_patch.json", code_patch),
            self.store.write_json(session_id, directory / "patch_review.json", patch_review),
            self.store.write_text(session_id, directory / "patch_error.txt", patch_error),
        ]

    def save_context_records(
        self, session_id: str, records: list[dict[str, Any]]
    ) -> list[Path]:
        paths: list[Path] = []
        manifest_path = self.store.session_directory(session_id) / "contexts" / "manifest.json"
        manifests: list[dict[str, Any]] = []
        if manifest_path.is_file():
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                manifests = loaded
        known = {str(item.get("fingerprint", "")) for item in manifests}
        next_index = max((int(item.get("index", 0)) for item in manifests), default=0) + 1
        for record in records:
            fingerprint = _context_fingerprint(record)
            if fingerprint in known:
                continue
            stage = "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in str(record["stage"])
            )
            index = next_index
            next_index += 1
            paths.append(
                self.store.write_text(
                    session_id,
                    Path("contexts") / f"{index:03d}_{stage}.md",
                    context_markdown(record),
                )
            )
            manifest = context_manifest(record, index)
            manifest["fingerprint"] = fingerprint
            manifest["file"] = f"{index:03d}_{stage}.md"
            manifests.append(manifest)
            known.add(fingerprint)
        paths.append(self.store.write_json(session_id, Path("contexts") / "manifest.json", manifests))
        return paths

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


def _merge_context(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            source = str(item.get("source", ""))
            if source in seen:
                continue
            seen.add(source)
            values.append(item)
    return values


def _context_fingerprint(record: dict[str, Any]) -> str:
    value = "\0".join(
        str(record.get(field, ""))
        for field in ("stage", "system_prompt", "user_prompt", "response")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _generation_notes(plan: dict[str, Any]) -> str:
    choices = list(plan.get("design_choices", []))
    missing = list(plan.get("missing_information", []))
    lines = ["# Generation notes", "", "## Model-made design choices", ""]
    lines.extend(f"- {item}" for item in choices)
    if not choices:
        lines.append("- None")
    lines.extend(["", "## Information still missing", ""])
    lines.extend(f"- {item}" for item in missing)
    if not missing:
        lines.append("- None")
    return "\n".join(lines) + "\n"
