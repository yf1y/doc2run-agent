# Optional domain scenario memory

Doc2Run Agent does not define power grids, databases, document layouts, or any
other domain in its core schema. When API documentation explains *how to call*
an SDK but not *what to build*, a selected domain can accumulate reviewed
scenario data without changing the general TaskSpec.

For example:

```text
knowledge/
├── api/
│   └── private_sdk.md
└── domains/
    └── your_domain/
        └── memory_schema.json
```

The schema hard-codes the scenario kind, allowed fields, required fields, JSON
types, and domain-specific forbidden keys. The power example in this directory
shows the format. It deliberately contains no SDK calls.

Run the CLI with `--domain your_domain`. After a successful result:

1. Keep giving normal instructions if the code still needs changes.
2. Enter `/approve` to start isolated scenario extraction and review.
3. Inspect the candidate, then enter `/remember` or `/reject-memory`.

Only `/remember` moves data to `memory/approved/<domain>/`. Generation searches
that exact domain only. Useful scenario fields include:

- the objects that normally make up one solution;
- how those objects relate to each other;
- exact parameters and reference data for the approved case.

Keep API signatures under `knowledge/api/`. Scenario memory rejects API/code
fields, source code, imports, and repair history.

An exact named reference case cannot be reconstructed reliably from API calls
alone. The first version therefore saves only facts supported by the approved
case; it does not infer a general rule from one example.
