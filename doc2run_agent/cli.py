"""Command-line adapter for configuring and interacting with Doc2Run Agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from .config import load_agent_model_settings
from .knowledge.retriever import LocalKnowledgeBase
from .knowledge.scenes import SceneLibrary
from .knowledge.tools import KnowledgeSearchTool, SceneSearchTool
from .llm import AgentModels, TextModel, create_agent_models
from .runtime.runner import LocalPythonRunner
from .schemas import SessionRecord
from .storage.sessions import FileSessionStore
from .workflow.orchestrator import Doc2RunOrchestrator


HELP_TEXT = """Commands:
  /confirm  confirm the TaskSpec and Scenario Plan, then start Code
  /approve [note]  run Memory: approve working code and save the plan as a Scene
  /show     show the current TaskSpec and Scenario Plan draft
  /history  show the Chat conversation
  /reset    archive this session and start it again
  /help     show this help
  /exit     leave; the session remains saved
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Doc2Run Agent: turn private documentation into verified Python automations."
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Persistent session name; omit to choose an existing or new session",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="YAML model configuration; defaults to ./doc2run_agent.yaml when present",
    )
    parser.add_argument("--sessions-dir", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=Path("domain_knowledge"),
        help="Knowledge root containing api/ and scenes/",
    )
    parser.add_argument("--max-fix-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--runtime-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Explicitly pass this environment variable to generated code; repeat as needed",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    session_id = args.session
    if session_id is None:
        session_id = select_session(args.sessions_dir)
    else:
        try:
            session_id = confirm_named_session(args.sessions_dir, session_id)
        except ValueError as error:
            parser.error(str(error))
    if session_id is None:
        return
    with create_agent_models(load_agent_model_settings(args.config)) as models:
        run_chat(
            models,
            session_id=session_id,
            sessions_directory=args.sessions_dir,
            knowledge_directory=args.knowledge_dir,
            max_fix_attempts=args.max_fix_attempts,
            timeout_seconds=args.timeout,
            top_k=args.top_k,
            runtime_environment=args.runtime_env,
        )


def confirm_named_session(
    sessions_directory: str | Path,
    session_id: str,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> str | None:
    """Continue a named session or confirm creation when the name is new."""

    store = FileSessionStore(sessions_directory)
    if store.has_session(session_id):
        return session_id
    try:
        answer = input_fn(f"Session '{session_id}' does not exist. Create it? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        output_fn("\nBye.")
        return None
    if answer.strip().lower() in {"y", "yes"}:
        return session_id
    output_fn("Session was not created.")
    return None


def select_session(
    sessions_directory: str | Path,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> str | None:
    """Let an interactive user continue an existing session or create a new one."""

    store = FileSessionStore(sessions_directory)
    session_ids = store.list_session_ids()
    if session_ids:
        output_fn("Saved sessions:")
        for index, session_id in enumerate(session_ids, start=1):
            output_fn(f"{index}. {session_id}")
        output_fn("Choose a session number, 'n' for a new session, or 'q' to exit.")
    else:
        output_fn("No saved sessions.")
        return _prompt_new_session(store, input_fn=input_fn, output_fn=output_fn)

    while True:
        try:
            choice = input_fn("session> ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\nBye.")
            return None
        if choice.lower() == "q":
            output_fn("Bye.")
            return None
        if choice.lower() == "n":
            return _prompt_new_session(store, input_fn=input_fn, output_fn=output_fn)
        if choice.isdigit() and 1 <= int(choice) <= len(session_ids):
            return session_ids[int(choice) - 1]
        if session_ids:
            output_fn("Enter one of the listed numbers, 'n', or 'q'.")


def _prompt_new_session(
    store: FileSessionStore,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> str | None:
    """Prompt for and validate a new session name without creating it yet."""

    while True:
        try:
            session_id = input_fn("new session name> ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\nBye.")
            return None
        if session_id.lower() == "q":
            output_fn("Bye.")
            return None
        try:
            store.session_directory(session_id)
        except ValueError as error:
            output_fn(f"Invalid session name: {error}")
            continue
        if store.has_session(session_id):
            output_fn("That session already exists; enter a different name or 'q' to exit.")
            continue
        return session_id


def run_chat(
    models: TextModel | AgentModels,
    *,
    session_id: str,
    sessions_directory: Path,
    knowledge_directory: Path,
    max_fix_attempts: int = 3,
    timeout_seconds: float = 10.0,
    top_k: int = 5,
    runtime_environment: tuple[str, ...] | list[str] = (),
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    store = FileSessionStore(sessions_directory)
    api_directory = knowledge_directory / "api"
    if not api_directory.is_dir():
        raise ValueError(
            f"API documentation directory does not exist: {api_directory}. "
            "Put API material under <knowledge-dir>/api/."
        )
    try:
        knowledge = LocalKnowledgeBase.from_directory(api_directory, source_prefix="api:")
    except ValueError as error:
        if str(error) != "Knowledge base is empty":
            raise
        raise ValueError(
            f"No usable API documentation was found in {api_directory}. "
            "Replace the template comments with real API/SDK material."
        ) from None
    scenes_directory = knowledge_directory / "scenes"
    scene_tool = SceneSearchTool.from_directory(scenes_directory)
    scene_library = SceneLibrary(scenes_directory)
    api_tool = KnowledgeSearchTool(
        knowledge,
        top_k=top_k,
        source_directory=api_directory,
        source_prefix="api:",
    )
    orchestrator = Doc2RunOrchestrator(
        models,
        api_tool,
        store,
        LocalPythonRunner(
            timeout_seconds=timeout_seconds,
            environment_keys=runtime_environment,
        ),
        max_fix_attempts=max_fix_attempts,
        scene_tool=scene_tool,
        scene_library=scene_library,
    )
    record = store.load_or_create(session_id)
    output_fn(
        _knowledge_summary(api_directory, scenes_directory, scene_tool)
    )
    output_fn(_welcome(record, store))

    while True:
        try:
            raw = input_fn("you> ")
        except (EOFError, KeyboardInterrupt):
            output_fn("\nSession saved. Bye.")
            return
        value = raw.strip()
        if not value:
            continue
        if value == "/exit":
            output_fn("Session saved. Bye.")
            return
        if value == "/help":
            output_fn(HELP_TEXT.rstrip())
            continue
        if value == "/show":
            output_fn(_format_spec(store.load_or_create(session_id)))
            continue
        if value == "/history":
            output_fn(_format_history(store.load_or_create(session_id)))
            continue
        if value == "/reset":
            store.reset(session_id)
            output_fn("Previous session archived; a new Chat session is ready.")
            continue
        known_action = (
            value == "/confirm"
            or value == "/approve"
            or value.startswith("/approve ")
        )
        if value.startswith("/") and not known_action:
            output_fn("Unknown command. Enter /help to see available commands.")
            continue

        try:
            record = store.load_or_create(session_id)
            should_refresh = value == "/confirm" or (
                not value.startswith("/") and record.phase in {"awaiting_review", "failed"}
            )
            if should_refresh:
                api_tool.refresh()
            if not value.startswith("/") and record.selected_scene is None:
                scene_tool.refresh()
            if value == "/confirm":
                result = orchestrator.confirm(session_id)
            elif value == "/approve" or value.startswith("/approve "):
                result = orchestrator.approve(session_id, value[len("/approve") :].strip())
            else:
                result = orchestrator.handle_message(session_id, value)
        except (ValueError, RuntimeError) as error:
            output_fn(f"error: {error}")
            continue
        output_fn(_format_result(result, store))


def _welcome(record: SessionRecord, store: FileSessionStore) -> str:
    location = store.session_directory(record.session_id)
    if record.messages:
        intro = f"Resumed session '{record.session_id}' in phase '{record.phase}'."
    else:
        intro = f"Started session '{record.session_id}'. Describe the automation you need."
    recovery = (
        "\nThe prior Code stage was interrupted; enter /confirm to retry from the confirmed spec."
        if record.phase in {"generating_code", "executing", "repairing"}
        else ""
    )
    return f"{intro}{recovery}\nArtifacts: {location}\nEnter /help for commands."


def _knowledge_summary(
    api_directory: Path,
    scenes_directory: Path,
    scene_tool: SceneSearchTool,
) -> str:
    lines = [f"API documentation: {api_directory}"]
    status = "loaded" if scene_tool.has_scenes else "no filled documents"
    lines.append(f"Scene library: {scenes_directory} [{status}]")
    return "\n".join(lines)


def _format_spec(record: SessionRecord) -> str:
    return (
        "TaskSpec:\n"
        + json.dumps(record.draft_spec.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n\nScenario Plan:\n"
        + (record.draft_plan or "(not ready)")
    )


def _format_history(record: SessionRecord) -> str:
    if not record.messages:
        return "No conversation yet."
    return "\n".join(f"{message.role}> {message.content}" for message in record.messages)


def _format_result(result: dict[str, object], store: FileSessionStore) -> str:
    parts = [f"agent> {result.get('assistant_message', result.get('status', 'completed'))}"]
    if result.get("status") == "awaiting_confirmation":
        record = SessionRecord.model_validate(result["session"])
        parts.extend(["", _format_spec(record), "\nEnter /confirm to generate and run."])
    if result.get("status") in {
        "awaiting_review", "memory", "approved", "succeeded", "failed"
    }:
        run_result = result.get("run_result")
        if isinstance(run_result, dict):
            stdout = str(run_result.get("stdout", "")).rstrip()
            stderr = str(run_result.get("stderr", "")).rstrip()
            if stdout:
                parts.extend(["\nstdout:", stdout])
            if stderr:
                parts.extend(["\nstderr:", stderr])
        session = SessionRecord.model_validate(result["session"])
        parts.append(f"\nArtifacts: {store.session_directory(session.session_id)}")
    scene_path = result.get("scene_path")
    if scene_path:
        parts.append(f"\nScene: {scene_path}")
    return "\n".join(parts)
