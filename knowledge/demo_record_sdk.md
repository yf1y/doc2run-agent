# Demo Record SDK

The package `doc2run_demo_sdk` represents a neutral private Python SDK. It is
installed together with this project and requires no credentials or network.

## Client

```python
from doc2run_demo_sdk import RecordClient

client = RecordClient("demo_records.json")
```

`RecordClient(database_path="demo_records.json")` stores data in a relative JSON
file. If the file does not exist, reads return two deterministic seed records.

## Read records

```python
records = client.list_records()
open_records = client.list_records(status="open")
record = client.get_record("1")
```

- `list_records(*, status: str | None = None) -> list[dict]`
- `get_record(record_id: str) -> dict`
- A record contains string fields `id`, `title`, and `status`.
- `get_record` raises `RecordNotFoundError` for an unknown ID.

## Create a record

```python
created = client.create_record(title="Review generated script", status="open")
```

`create_record(*, title: str, status: str = "open") -> dict` appends a record to
the relative database file and returns the new record. It is the only method with
a persistent side effect.

## JSON output

For machine-readable stdout, use `json.dumps(value, ensure_ascii=False)` and print
exactly one JSON value. Do not print progress messages before the JSON value.
