"""Persist planning, retrieval, model-context, generation, and execution artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .sessions import FileSessionStore


class ArtifactManager:
    """Own the on-disk artifact layout for one or more sessions."""

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

    def save_selected_scene(
        self, session_id: str, selected_scene: dict[str, Any] | None
    ) -> Path:
        if not selected_scene:
            content = "# Selected Scene\n\nNo Scene document was available.\n"
        else:
            heading = f" — {selected_scene['heading']}" if selected_scene.get("heading") else ""
            content = (
                "# Selected Scene\n\n"
                f"Source: {selected_scene['source']}{heading}\n\n"
                + str(selected_scene["content"]).strip()
                + "\n"
            )
        return self.store.write_text(
            session_id, Path("planning") / "selected_scene.md", content
        )

    def save_scenario_plan(self, session_id: str, plan: str) -> Path:
        return self.store.write_text(
            session_id,
            Path("planning") / "scenario_plan.md",
            (plan.strip() + "\n") if plan.strip() else "# Scenario Plan\n\nNot ready.\n",
        )

    def save_api_context(
        self, session_id: str, context: list[dict[str, Any]]
    ) -> Path:
        return self.store.write_text(
            session_id,
            Path("planning") / "api_context.md",
            "# Selected API documentation\n\n" + _format_retrieved_context(context) + "\n",
        )

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

    def save_refinement_conflict(
        self,
        session_id: str,
        *,
        instruction: str,
        fix_plan: dict[str, Any],
    ) -> Path:
        return self.store.write_json(
            session_id,
            Path("planning") / "refinement_conflict.json",
            {"instruction": instruction, "fix_plan": fix_plan},
        )

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
                    _context_markdown(record),
                )
            )
            manifest = _context_manifest(record, index)
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


def _context_fingerprint(record: dict[str, Any]) -> str:
    value = "\0".join(
        str(record.get(field, ""))
        for field in ("stage", "system_prompt", "user_prompt", "response")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _format_retrieved_context(context: list[dict[str, Any]]) -> str:
    """Persist the complete selected context; prompt budgeting happens in agents."""

    if not context:
        return "(no documentation retrieved)"
    parts: list[str] = []
    for item in context:
        heading = f" — {item['heading']}" if item.get("heading") else ""
        parts.append(f"[Source: {item['source']}{heading}]\n{item['content']}")
    return "\n\n".join(parts)


def _context_manifest(record: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "stage": record["stage"],
        "estimated_tokens": record["estimated_tokens"],
        "sources": list(record.get("sources", [])),
    }


def _context_markdown(record: dict[str, Any]) -> str:
    return (
        f"# {record['stage']}\n\n"
        f"Estimated input tokens: {record['estimated_tokens']}\n\n"
        "## System prompt\n\n"
        + _fenced(str(record["system_prompt"]), "text")
        + "\n\n## User prompt\n\n"
        + _fenced(str(record["user_prompt"]), "text")
        + "\n\n## Model response\n\n"
        + _fenced(str(record["response"]), "text")
        + "\n"
    )


def _fenced(value: str, language: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=2)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{value}\n{fence}"
