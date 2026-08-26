[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

[![Tests](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Turn private API/SDK documentation and a natural-language request into an executed, repairable, and traceable Python script.

```text
request → clarify and confirm → retrieve docs → plan and review
        → generate → validate → execute → edit and retest
        → user approval → optional domain-scoped memory
```

Doc2Run Agent targets Python users who need queries, reports, configuration checks, private-SDK examples, or other low-frequency automations. It is neither a general coding agent nor a secure execution service for untrusted input.

## What it does

- Confirms goals, inputs, outputs, limits, and success criteria before generation.
- Keeps API documentation, user-supplied domain material, and approved scenarios separate.
- Reviews an implementation plan before generation and stops if the final review still fails.
- Validates and executes generated code, preserving stdout, stderr, and every revision.
- Applies focused edits, reviews them, and reruns the script after failures or user feedback.
- Creates memory only after `/approve`, in a fresh context, followed by format checks, independent review, and explicit `/remember` confirmation.
- Persists sessions, specifications, model contexts, retrieval results, and run artifacts locally.

## Install

Python 3.10 or newer is required.

```bash
git clone https://github.com/yf1y/doc2run-agent.git
cd doc2run-agent
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
pytest -q
```

## Prepare a project directory

[`demo/`](demo/) is an empty template. It contains no business example, sample request, or mock SDK. Copy it and replace its contents:

```bash
# macOS / Linux
cp -R demo my_project
cp my_project/.env.example my_project/.env

# Windows PowerShell
Copy-Item -Recurse demo my_project
Copy-Item my_project/.env.example my_project/.env
```

```text
my_project/
├── doc2run_agent.yaml          # model and request settings
├── .env                        # model secret; never commit it
└── knowledge/
    ├── api/                    # required: how code calls the API
    │   ├── setup.md
    │   ├── api_reference.md
    │   └── usage_rules.md
    └── domains/                # optional vertical-domain material
        └── your_domain/        # rename to the --domain value
            ├── docs/
            │   └── domain_knowledge.md
            └── memory_schema.json
```

Set a LiteLLM-compatible model in `doc2run_agent.yaml`, put the referenced secret in `.env`, and replace the Markdown comments with real material. Template comments are ignored; startup fails clearly when no usable API documentation exists.

Knowledge is kept in three separate places:

- `knowledge/api/` contains setup, exact signatures, schemas, exceptions, and call restrictions: how code calls the API.
- `knowledge/domains/<domain>/docs/` contains terms, business rules, layouts, mappings, topology, and reference data: what is correct in that domain.
- `memory/approved/<domain>/` is managed by the program and contains only user-approved, validated, independently reviewed scenarios.

Do not place real credentials or sensitive production data in the knowledge directory.

## Run

```bash
doc2run-agent \
  --session my-project \
  --config my_project/doc2run_agent.yaml \
  --knowledge-dir my_project/knowledge \
  --domain your_domain
```

Omit the last line when domain material and scenario memory are not needed. At startup, the CLI prints the exact API, domain, and memory locations it loaded. Enter the request directly; no `request.txt` is required.

```text
/show             show the current requirement
/history          show the requirements conversation
/confirm          confirm and start generation, validation, and execution
/approve [note]   accept the current successful version
/remember         save a reviewed scenario candidate
/reject-memory    reject the candidate
/reset            archive the session and start again
/help             show help
/exit             save and leave
```

A successful run does not force the session to end. Continue describing changes to edit, review, and rerun the current code. `/approve` ends the refinement stage. If domain memory is enabled, candidate extraction uses a separate fresh context.

The detailed Chinese guide is available in [`使用文档.md`](使用文档.md).

## Optional domain memory

Domain-document retrieval and scenario memory are disabled unless `--domain` is provided. To enable them, fill:

```text
my_project/knowledge/domains/<domain>/docs/
my_project/knowledge/domains/<domain>/memory_schema.json
```

`docs/` contains authoritative domain material supplied in advance. The schema defines which fields may later be learned from approved results. API signatures, imports, source code, credentials, and repair history are rejected. Domains remain isolated.

## Artifacts and recovery

Each session keeps its state and artifacts under `sessions/<session-id>/`, including confirmed specifications, retrieval results, plans, exact model contexts, code revisions, validation output, stdout, and stderr. Reuse the same `--session` value to continue; `/reset` archives the previous state instead of deleting it.

## Security boundary

The local runner performs policy checks, trims the environment, and enforces a timeout, but it is not an operating-system sandbox. Do not expose it as a public executor for untrusted requests. Production use requires a restricted container or VM plus controlled credential, network, filesystem, and subprocess access.

## Repository layout

```text
doc2run-agent/
├── doc2run_agent/              # project source code
├── demo/                       # copyable empty project template
├── tests/                      # automated tests
├── 使用文档.md                 # detailed guide
├── README.md
├── README_EN.md
└── pyproject.toml
```

Built with [LangGraph](https://github.com/langchain-ai/langgraph) and [LiteLLM](https://github.com/BerriAI/litellm), under the [MIT License](LICENSE).
