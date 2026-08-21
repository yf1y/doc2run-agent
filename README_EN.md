[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

[![Tests](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Turn “read the docs, write the code, make it run” into one complete loop.**

Put your private SDK or API documentation in `knowledge/`, then describe the automation you need in plain language. Doc2Run Agent clarifies the requirements, retrieves the right documentation, generates Python, validates and runs it, and diagnoses and repairs failures.

The result is not merely code that looks plausible. It is an **executed, traceable, resumable automation** with evidence for every step.

```text
Your request → clarify → confirm → retrieve private docs → generate
             → validate → execute → repair failures → persist artifacts
```

| Typical one-shot generation | Doc2Run Agent |
|---|---|
| Guesses from a short prompt | Confirms goals, I/O, constraints, and acceptance criteria first |
| May invent private SDK usage | Retrieves local documentation before generation and repair |
| Stops after printing code | Validates, executes, and captures stdout/stderr |
| Hands failures back to you | Classifies errors and repairs within a hard retry limit |
| Leaves little evidence | Versions the spec, retrieval context, code, and every run |

An interaction starts like this:

```text
you> Read all open records from our internal Record SDK and print them as JSON.

agent> Where should the output go? May the task modify data? What counts as success?

you> stdout; read-only; valid JSON with id, title, and status on every item.

agent> The TaskSpec is ready. Review it and enter /confirm.

you> /confirm

agent> Documentation retrieval, generation, validation, and execution completed.
```

[Overview](#1-overview) · [Installation](#2-installation) · [Usage](#3-usage) · [Project structure](#4-project-structure)

---

## 1. Overview

Doc2Run Agent separates an automation task into three focused stages:

| Stage | Responsibility | Why it matters |
|---|---|---|
| **Requirements Agent** | Clarifies the request and builds a typed `TaskSpec` | Prevents coding against vague requirements |
| **Generation Agent** | Plans retrieval, reads documentation, and generates a complete script | Grounds private API usage in actual docs |
| **Fix Agent** | Diagnoses validation/runtime failures, retrieves targeted context, and rewrites | Keeps the workflow moving after the first failure |

Execution is controlled by deterministic Python code, not by another LLM agent. Generation begins only after all required sections are explicit and the user enters `/confirm`.

Key capabilities:

- **Requirements confirmation gate** for goals, I/O, constraints, and acceptance criteria.
- **Local documentation RAG** over `.md`, `.txt`, `.json`, and `.jsonl` files with source-tagged evidence.
- **Independent model selection** for requirements, generation, and repair—or one shared model.
- **Generate–validate–execute–repair loop** with syntax, import, destructive-call, and absolute-write checks.
- **Resumable sessions** that preserve conversation and workflow state across restarts.
- **Complete artifacts** including specs, retrieved context, generated code, validation, stdout, stderr, and repair history.

A local `doc2run_demo_sdk` is bundled so the full workflow can be tried without credentials or an external service.

## 2. Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/yf1y/doc2run-agent.git
cd doc2run-agent
python -m venv .venv
```

Activate the environment:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install and prepare local configuration:

```bash
pip install -e .

# macOS / Linux
cp config.example.yaml doc2run_agent.yaml
cp .env.example .env

# Windows PowerShell
Copy-Item config.example.yaml doc2run_agent.yaml
Copy-Item .env.example .env
```

For development and tests:

```bash
pip install -e ".[dev]"
pytest -q
```

## 3. Usage

### 3.1 Configure a model

Doc2Run Agent uses LiteLLM and works with OpenAI, Anthropic, Gemini, Azure, Ollama, OpenRouter, and other providers.

The simplest configuration shares one model across all three stages. In `doc2run_agent.yaml`:

```yaml
models:
  defaults:
    model: openai/gpt-5
    api_key_env: OPENAI_API_KEY
    timeout: 120
    max_retries: 2
```

Store the credential in `.env`:

```dotenv
OPENAI_API_KEY=your-key-here
```

Each stage can also use a different model:

```yaml
models:
  defaults:
    timeout: 120
    max_retries: 2

  requirements:
    model: anthropic/claude-sonnet-4-5
    api_key_env: ANTHROPIC_API_KEY

  code:
    model: openai/gpt-5
    api_key_env: OPENAI_API_KEY
    timeout: 180

  fix:
    model: ollama/qwen2.5-coder
    api_base: http://localhost:11434
```

`doc2run_agent.yaml` and `.env` are gitignored. Never commit real credentials.

### 3.2 Add your documentation

Place SDK/API references under `knowledge/`:

```text
knowledge/
├── internal_sdk.md
├── api_reference.json
└── usage_notes.txt
```

Before generation and repair, Doc2Run Agent plans focused queries and sends only the most relevant chunks to the model. Keep the bundled `demo_record_sdk.md` for a credential-free first run.

### 3.3 Run the interactive CLI

```bash
doc2run-agent --session demo
```

Describe one automation and answer the focused follow-up questions. Once the resulting `TaskSpec` is ready, enter `/confirm` to begin generation and execution.

```text
/show      show the current TaskSpec draft
/history   show the saved requirements conversation
/confirm   generate, validate, run, and repair
/reset     archive the current session and start again
/help      show command help
/exit      save and exit
```

Resume by reusing the same session ID:

```bash
doc2run-agent --session demo
```

Select another configuration or knowledge directory when needed:

```bash
doc2run-agent \
  --session internal-report \
  --config configs/development.yaml \
  --knowledge-dir knowledge
```

See [`examples/requests.md`](examples/requests.md) for another example.

### 3.4 Inspect the result

Every session gets an isolated artifact directory:

```text
sessions/<session-id>/
├── session.json                 # conversation and workflow state
├── task_specs/                  # immutable confirmed spec versions
├── retrieval/                   # retrieval evidence for every round
├── runs/                        # generated/repaired code and run results
└── workspace/generated.py       # final executed script
```

`/reset` archives the previous session under `sessions/archives/` instead of deleting it.

> [!WARNING]
> The current runner provides AST policy checks, a reduced environment, and timeouts, but it is **not an OS sandbox**. Do not execute untrusted requests or expose it directly as a public service. Replace `LocalPythonRunner` with a locked-down container or VM for that use case.

## 4. Project structure

```text
doc2run-agent/
├── src/
│   ├── doc2run_agent/
│   │   ├── requirements_agent.py  # requirement clarification and TaskSpec
│   │   ├── generation_agent.py    # documentation retrieval and generation
│   │   ├── fix_agent.py           # failure diagnosis and repair
│   │   ├── orchestrator.py        # top-level workflow and state gates
│   │   ├── retriever.py           # local knowledge retrieval
│   │   ├── validation.py          # deterministic static validation
│   │   ├── runner.py              # timeout-controlled execution
│   │   ├── session_store.py       # persistence and recovery
│   │   ├── artifacts.py           # artifact organization
│   │   ├── config.py / llm.py     # model configuration and LiteLLM adapter
│   │   └── cli.py                 # interactive CLI
│   └── doc2run_demo_sdk/          # offline demo SDK
├── knowledge/                     # SDK/API documentation
├── examples/                      # example requests
├── tests/                         # deterministic test suite
├── config.example.yaml            # model configuration example
└── pyproject.toml                 # package, dependencies, and CLI entry point
```

Built with [LangGraph](https://github.com/langchain-ai/langgraph) and [LiteLLM](https://github.com/BerriAI/litellm). Licensed under the [MIT License](LICENSE).
