# Example conversation

This example exercises the intended application: generating an automation script
from private SDK documentation.

```text
you> Generate a Python automation that lists open records through the documented demo SDK and prints them as JSON.
agent> ...follow-up questions about inputs, outputs, side effects, and acceptance...

you> It takes no input. Print one JSON array to stdout. Read-only, use only the standard library and doc2run_demo_sdk. It passes when the output parses as JSON and every item has id, title, and status.
agent> ...completed TaskSpec...

you> /confirm
agent> Code generation and execution completed successfully.
```

The exact questions vary because requirements interpretation is model-driven. The
workflow itself always requires all four sections to be explicitly confirmed
before `/confirm` can start code generation.
