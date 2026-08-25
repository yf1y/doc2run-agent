[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

[![Tests](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Turn “read the docs, write the code, make it run” into one complete loop.**

Put your private SDK or API documentation in `knowledge/`, then describe the automation you need in plain language. Doc2Run Agent clarifies the requirements, retrieves the right documentation, generates Python, validates and runs it, and diagnoses and repairs failures.

The result is not merely code that looks plausible. It is an **executed, traceable, resumable automation** with evidence for every step.

```text
Your request → clarify → confirm → retrieve docs → write and review a plan
             → retrieve missing details → generate → validate → execute
             → apply and review a local repair → user review → optional scenario memory
```

| Typical one-shot generation | Doc2Run Agent |
|---|---|
| Guesses from a short prompt | Confirms goals, I/O, constraints, and acceptance criteria first |
| May invent private SDK usage | Organizes API material and reviews a sourced implementation plan first |
| Stops after printing code | Validates, executes, and captures stdout/stderr |
| Hands failures back to you | Plans a small edit, checks it, and repairs within a hard retry limit |
| Leaves little evidence | Saves plans, exact model contexts, code, and every run |

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

Doc2Run Agent divides an automation task into three connected stages:

| Stage | Responsibility | Why it matters |
|---|---|---|
| **Requirements Agent** | Clarifies the request and builds a typed `TaskSpec` | Prevents coding against vague requirements |
| **Generation Agent** | Retrieves documentation, writes and reviews an implementation plan, then generates a script | Separates document understanding from coding for smaller models |
| **Fix Agent** | Plans, retrieves, applies, and reviews a local code edit | Preserves working code instead of rewriting everything by default |

Execution is controlled by deterministic Python code, not by another LLM agent. Generation begins only after all required sections are explicit and the user enters `/confirm`.

Key capabilities:

- **Requirements confirmation gate** for goals, I/O, constraints, and acceptance criteria.
- **Separated local retrieval** for API documentation and approved scenarios, plus a targeted second API search when plan review finds a gap.
- **Optional domain memory** outside the general `TaskSpec`; each domain provides its own hard schema for reusable scenario data.
- **Plan before code** with JSON artifacts that expose missing information before generation.
- **Controlled local repair** with exact replacements, a review step, and full rewrite only as a later fallback.
- **Traceable contexts** containing the exact prompts, responses, sources, and estimated input tokens for each model call.
- **Independent model selection** for requirements, generation, and repair—or one shared model.
- **Review before memory**: keep refining a successful run, then use `/approve` to extract a scenario candidate in a fresh context; deterministic checks, an independent review, and `/remember` are all required before reuse.
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
    context_tokens: 16000
```

`context_tokens` is the workflow's estimated input limit for one model call. An oversized call fails explicitly instead of silently truncating the TaskSpec, code, or API signatures. It can also be configured per stage.

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
├── api/
│   ├── internal_sdk.md
│   └── api_reference.json
└── domains/                     # optional
    └── power/
        ├── overview.md
        ├── building_rules.md
        ├── examples.json
        └── memory_schema.json       # optional hard schema for reusable scenario data
```

The optional domain directory can provide general concepts, construction rules, and examples without changing the core workflow schema. Before generation, Doc2Run Agent retrieves relevant material, reviews an implementation plan, and searches again when the review finds a gap. Keep the bundled `demo_record_sdk.md` for a credential-free first run.

### 3.3 Run the interactive CLI

```bash
doc2run-agent --session demo
```

Describe one automation and answer the focused follow-up questions. Once the resulting `TaskSpec` is ready, enter `/confirm` to begin generation and execution. After a successful run, enter a normal instruction to refine the code and run it again, or approve it when satisfied.

```text
/show      show the current TaskSpec draft
/history   show the saved requirements conversation
/confirm   generate, validate, run, and repair
/approve [note]  approve the code and, with a domain, create an isolated memory candidate
/remember  add the reviewed candidate to the active domain
/reject-memory  reject and archive the candidate
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
  --knowledge-dir knowledge \
  --domain power \
  --memory-dir memory
```

Without `--domain`, scenario-memory reads and writes are disabled. A selected domain requires `knowledge/domains/<domain>/memory_schema.json`; the power example under `examples/domain_knowledge/` is a starting point. API documentation is retrieved only from `knowledge/api/`, while accepted scenarios come only from `memory/approved/<domain>/`.

See [`examples/requests.md`](examples/requests.md) for another example.

### 3.4 Inspect the result

Every session gets an isolated artifact directory:

```text
sessions/<session-id>/
├── session.json                 # conversation and workflow state
├── decisions.md                 # explicit user choices and corrections
├── task_specs/                  # immutable confirmed spec versions
├── retrieval/                   # initial, follow-up, and repair searches
├── planning/                    # selected docs, plan, review, and disclosed model choices
├── contexts/                    # exact model inputs and outputs
├── runs/                        # code, run results, edit plans, and edit reviews
└── workspace/generated.py       # final executed script
```

`/reset` archives the previous session under `sessions/archives/` instead of deleting it.

Scenario memory has two user gates. `/approve` creates a candidate under `memory/candidates/`; even after deterministic schema validation and an independent model review, `/remember` is required to move it into `memory/approved/`. Rejected candidates are archived under `memory/rejected/`. The hard domain schema permits scenario data only—never API signatures, source code, or repair history.

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
│   │   ├── memory_agent.py        # isolated extraction and independent review
│   │   ├── memory_store.py        # schemas, approval, and domain-isolated retrieval
│   │   ├── orchestrator.py        # top-level workflow and state gates
│   │   ├── retriever.py           # local knowledge retrieval
│   │   ├── context.py             # context budgets, log trimming, and call records
│   │   ├── code_edits.py          # exact local code replacement
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
