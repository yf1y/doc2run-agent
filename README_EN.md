[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

Doc2Run Agent turns one reusable Scene, private API/SDK documentation, and a natural-language request into a verified, traceable Python script.

## 1. Overview

```text
Chat   → retrieve one Scene, clarify the request, and confirm a Scenario Plan
Code   → search API docs, generate code, validate, and run
Fix    → retrieve relevant API docs, patch locally, review, and rerun
Memory → after approval, persist only the confirmed plan as a reusable Scene
```

The Chat stage selects exactly one Scene document. Code and Fix search only API/SDK knowledge; they never mix API signatures into Scene data. The confirmed Scenario Plan is the cross-stage contract and is passed to Code unchanged. Memory is deterministic: it does not call a model and has no separate model configuration.

## 2. Installation

Python 3.10 or newer is required:

```bash
git clone https://github.com/yf1y/doc2run-agent.git
cd doc2run-agent
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -e .
```

## 3. Usage

Copy [`demo/`](demo/) and fill in the two knowledge areas:

```text
my_project/
├── doc2run_agent.yaml
├── .env
└── domain_knowledge/
    ├── api/                         # searched by Code/Fix
    │   ├── setup.md
    │   ├── api_reference.md
    │   └── usage_rules.md
    └── scenes/                      # one complete file per reusable Scene
        └── scene_5_nodes.md
```

Supported files are `.md`, `.txt`, `.json`, `.jsonl`, `.yaml`, and `.yml`. API files are chunked for retrieval. Scenes are ranked at document level; only the top one is loaded in full.

A Scene should describe the scenario, not API calls:

```markdown
# Goal
# Components
# Arrangement and connections
# Node numbering and parameters
# Invariants
# Generalization rules
# Output and acceptance
```

Configure the model in `doc2run_agent.yaml`:

```yaml
models:
  defaults:
    model: your-provider/your-model
    api_key_env: MODEL_API_KEY
    timeout: 120
    max_retries: 3
    max_tokens: 4000
    context_tokens: 16000
```

Run:

```bash
doc2run-agent \\
  --session my-project \\
  --config my_project/doc2run_agent.yaml \\
  --knowledge-dir my_project/domain_knowledge
```

If `--session` is omitted, startup shows only the saved session names: choose a number to continue one, `n` to create a new session, or `q` to exit. When there are no saved sessions, it asks for a new name directly. An existing `--session` continues directly; a new name requires confirmation before it is created.

Use `/show` to inspect the TaskSpec and Scenario Plan, `/confirm` to freeze them, and `/approve` after a successful run to save the plan directly under `scenes/`. `/reset` starts a new session; `/exit` saves and exits.

## 4. Project structure

```text
doc2run-agent/
├── doc2run_agent/
│   ├── cli.py                  # CLI entry point and interaction adapter
│   ├── config.py               # YAML/.env model configuration
│   ├── llm.py                  # LiteLLM adapter, retries, and token limits
│   ├── schemas.py              # shared cross-module contracts
│   ├── agents/                 # Chat, Code, Fix, and Memory stages
│   │   ├── chat.py             # Scene injection and Scenario Plan
│   │   ├── code.py             # API retrieval, generation, validation
│   │   ├── fix.py              # API-grounded repair and patch review
│   │   ├── memory.py           # approved-plan persistence
│   │   ├── prompts.py           # Agent prompts
│   │   ├── context.py           # model context budgets and audit records
│   │   └── parsing.py            # structured model-output parsing
│   ├── knowledge/              # API/Scene retrieval and Scene persistence
│   │   ├── retriever.py        # local indexing and ranking
│   │   ├── tools.py            # API and Scene search tools
│   │   └── scenes.py           # Memory: approved plans as Scene files
│   ├── runtime/                # validation, execution, edits, failures
│   ├── storage/                # sessions, files, and run artifacts
│   └── workflow/               # top-level LangGraph orchestration
│       └── orchestrator.py
├── domain_knowledge/           # API and Scene documents in a user project
├── demo/                        # copyable template
└── tests/                      # logic tests
```

The main dependency direction is `cli → workflow`; `workflow` coordinates `agents`, `knowledge`,
`runtime`, and `storage`, while lower modules do not import `workflow`. All cross-stage state travels
through the independent `schemas.py`.

Runner timeout and environment-variable allowlisting are not an OS-level sandbox. Use a restricted container or VM for untrusted or production execution.
