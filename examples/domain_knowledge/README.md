# Optional domain knowledge

Doc2Run Agent does not define power grids, databases, document layouts, or any
other domain in its core schema.  When API documentation explains *how to call*
an SDK but not *what to build*, add ordinary Markdown or JSON files under a
domain subdirectory in the selected knowledge directory.

For example:

```text
knowledge/
├── api/
│   └── private_sdk.md
└── domains/
    └── your_domain/
        ├── overview.md
        ├── building_rules.md
        ├── review_rules.md
        └── examples.json
```

Useful content includes:

- the objects that normally make up one solution;
- how those objects relate to each other;
- the order in which they should be created;
- general construction rules that apply beyond one example;
- checks a domain expert would perform before accepting a result;
- the source of any exact reference data.

Keep API signatures in the API documentation.  Keep domain rules and reference
data in the domain directory so the implementation plan can distinguish them.

An exact named reference case cannot be reconstructed reliably from API calls
alone.  Include its data, allow another approved source, or require the user to
provide it.  General rules are appropriate when the task permits the model to
design a new valid case instead of reproducing an exact standard case.
