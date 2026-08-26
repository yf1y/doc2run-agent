import json

from doc2run_agent.runner import LocalPythonRunner
from doc2run_demo_sdk import RecordClient, RecordNotFoundError


def test_demo_sdk_reads_filters_and_persists_records(tmp_path):
    client = RecordClient(tmp_path / "records.json")

    assert [item["id"] for item in client.list_records(status="open")] == ["1"]
    created = client.create_record(title="Generated task")

    assert created["id"] == "3"
    assert RecordClient(tmp_path / "records.json").get_record("3") == created


def test_demo_sdk_reports_unknown_record(tmp_path):
    client = RecordClient(tmp_path / "records.json")

    try:
        client.get_record("999")
    except RecordNotFoundError as error:
        assert error.args == ("999",)
    else:
        raise AssertionError("Expected RecordNotFoundError")


def test_bundled_demo_sdk_runs_inside_the_local_runner(tmp_path):
    code = """from doc2run_demo_sdk import RecordClient
import json

records = RecordClient().list_records(status="open")
print(json.dumps(records))
"""

    result = LocalPythonRunner(timeout_seconds=2).run(code, tmp_path / "workspace")

    assert result.ok is True
    assert json.loads(result.stdout) == [
        {"id": "1", "title": "Prepare weekly report", "status": "open"}
    ]
