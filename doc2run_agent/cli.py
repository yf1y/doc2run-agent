from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from .config import load_agent_model_settings
from .knowledge_tools import KnowledgeSearchTool
from .llm import AgentModels, TextModel, create_agent_models
from .memory_store import ScenarioMemoryStore
from .orchestrator import Doc2RunOrchestrator
from .retriever import LocalKnowledgeBase
from .runner import LocalPythonRunner
from .schemas import SessionRecord
from .session_store import FileSessionStore


HELP_TEXT = """Commands:
  /confirm  confirm the completed TaskSpec and start generation
  /approve [note]  approve the working code and create a reviewed memory candidate
  /remember  add the reviewed candidate to the active domain
  /reject-memory  reject and archive the memory candidate
  /show     show the current TaskSpec draft
  /history  show the requirements conversation
  /reset    archive this session and start it again
  /help     show this help
  /exit     leave; the session remains saved
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Doc2Run Agent: turn private documentation into verified Python automations."
    )
    parser.add_argument("--session", default="default", help="Persistent session identifier")
    parser.add_argument(
        "--config",
        type=Path,
        help="YAML model configuration; defaults to ./doc2run_agent.yaml when present",
    )
    parser.add_argument("--sessions-dir", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=Path("knowledge"),
        help="Knowledge root containing api/ and optional domains/<domain>/docs/",
    )
    parser.add_argument("--memory-dir", type=Path, default=Path("memory"))
    parser.add_argument(
        "--domain", default="", help="Optional domain name; enables isolated scenario memory"
    )
    parser.add_argument("--max-fix-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--top-k", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    with create_agent_models(load_agent_model_settings(args.config)) as models:
        run_chat(
            models,
            session_id=args.session,
            sessions_directory=args.sessions_dir,
            knowledge_directory=args.knowledge_dir,
            memory_directory=args.memory_dir,
            domain=args.domain,
            max_fix_attempts=args.max_fix_attempts,
            timeout_seconds=args.timeout,
            top_k=args.top_k,
        )


def run_chat(
    models: TextModel | AgentModels,
    *,
    session_id: str,
    sessions_directory: Path,
    knowledge_directory: Path,
    memory_directory: Path = Path("memory"),
    domain: str = "",
    max_fix_attempts: int = 3,
    timeout_seconds: float = 10.0,
    top_k: int = 5,
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
    scenario_memory = ScenarioMemoryStore(memory_directory, knowledge_directory / "domains")
    domain_knowledge_tool: KnowledgeSearchTool | None = None
    domain_directory: Path | None = None
    if domain:
        scenario_memory.load_schema(domain)
        domain_directory = knowledge_directory / "domains" / domain / "docs"
        if domain_directory.is_dir():
            try:
                domain_knowledge = LocalKnowledgeBase.from_directory(
                    domain_directory, source_prefix=f"domain:{domain}:"
                )
            except ValueError as error:
                if str(error) != "Knowledge base is empty":
                    raise
            else:
                domain_knowledge_tool = KnowledgeSearchTool(domain_knowledge, top_k=top_k)
    orchestrator = Doc2RunOrchestrator(
        models,
        KnowledgeSearchTool(knowledge, top_k=top_k),
        store,
        LocalPythonRunner(timeout_seconds=timeout_seconds),
        max_fix_attempts=max_fix_attempts,
        scenario_memory=scenario_memory,
        domain=domain,
        domain_knowledge_tool=domain_knowledge_tool,
    )
    record = store.load_or_create(session_id)
    output_fn(
        _knowledge_summary(
            api_directory, domain, domain_directory, domain_knowledge_tool, memory_directory
        )
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
            output_fn("Previous session archived; a new requirements session is ready.")
            continue
        known_action = (
            value == "/confirm"
            or value == "/remember"
            or value == "/reject-memory"
            or value == "/approve"
            or value.startswith("/approve ")
        )
        if value.startswith("/") and not known_action:
            output_fn("Unknown command. Enter /help to see available commands.")
            continue

        try:
            if value == "/confirm":
                result = orchestrator.confirm(session_id)
            elif value == "/remember":
                result = orchestrator.remember(session_id)
            elif value == "/reject-memory":
                result = orchestrator.reject_memory(session_id)
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
        "\nThe prior generation was interrupted; enter /confirm to retry from the confirmed spec."
        if record.phase in {"generating_code", "executing", "repairing"}
        else ""
    )
    return f"{intro}{recovery}\nArtifacts: {location}\nEnter /help for commands."


def _knowledge_summary(
    api_directory: Path,
    domain: str,
    domain_directory: Path | None,
    domain_tool: KnowledgeSearchTool | None,
    memory_directory: Path,
) -> str:
    lines = [f"API documentation: {api_directory}"]
    if not domain:
        lines.append("Domain documentation and approved scenario memory: disabled")
    else:
        status = "loaded" if domain_tool is not None else "no filled documents"
        lines.append(f"Domain documentation ({domain}): {domain_directory} [{status}]")
        lines.append(f"Approved scenario memory: {memory_directory / 'approved' / domain}")
    return "\n".join(lines)


def _format_spec(record: SessionRecord) -> str:
    return json.dumps(record.draft_spec.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _format_history(record: SessionRecord) -> str:
    if not record.messages:
        return "No conversation yet."
    return "\n".join(f"{message.role}> {message.content}" for message in record.messages)


def _format_result(result: dict[str, object], store: FileSessionStore) -> str:
    parts = [f"agent> {result.get('assistant_message', result.get('status', 'completed'))}"]
    if result.get("status") == "awaiting_confirmation":
        record = SessionRecord.model_validate(result["session"])
        parts.extend(["\nTaskSpec:", _format_spec(record), "\nEnter /confirm to generate and run."])
    if result.get("status") == "plan_rejected":
        session = SessionRecord.model_validate(result["session"])
        parts.append(f"\nArtifacts: {store.session_directory(session.session_id)}")
    if result.get("status") in {
        "awaiting_review", "memory_candidate_ready", "approved", "succeeded", "failed"
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
    candidate_path = result.get("memory_candidate_path")
    if candidate_path:
        parts.append(f"\nMemory candidate: {candidate_path}")
        parts.append("Review it, then enter /remember or /reject-memory.")
    memory_path = result.get("memory_path")
    if memory_path:
        parts.append(f"\nMemory record: {memory_path}")
    return "\n".join(parts)
