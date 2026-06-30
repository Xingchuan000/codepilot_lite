import json

from codepilot.trace.events import TraceEvent


def test_trace_event_serializes() -> None:
    event = TraceEvent(
        run_id="run-test",
        step=1,
        event_type="tool_call",
        tool_name="list_files",
        input={"path": "."},
        success=True,
    )

    data = event.model_dump()

    assert data["schema_version"] == "trace.v1"
    assert data["run_id"] == "run-test"
    assert data["step"] == 1
    assert data["event_type"] == "tool_call"
    assert data["tool_name"] == "list_files"
    assert data["timestamp"]


def test_trace_event_model_dump_json_is_valid_json() -> None:
    event = TraceEvent(run_id="run-test", step=1, event_type="run_start")

    data = json.loads(event.model_dump_json())

    assert data["schema_version"] == "trace.v1"
    assert data["event_type"] == "run_start"
    assert data["tool_name"] is None
