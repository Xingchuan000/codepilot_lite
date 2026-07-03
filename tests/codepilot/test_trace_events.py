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


def test_trace_event_supports_policy_decision_fields() -> None:
    event = TraceEvent(
        run_id="run-test",
        step=1,
        event_type="policy_decision",
        tool_name="run_shell",
        policy_decision="deny",
        policy_reason="blocked",
        policy_rule="command.deny_substrings.rm -rf",
        policy_mode="build",
    )

    data = event.model_dump()

    assert data["event_type"] == "policy_decision"
    assert data["policy_decision"] == "deny"
    assert data["policy_reason"] == "blocked"
    assert data["policy_rule"] == "command.deny_substrings.rm -rf"
    assert data["policy_mode"] == "build"


def test_trace_event_accepts_new_agent_and_llm_types() -> None:
    for event_type in ("llm_call", "agent_action", "agent_observation", "agent_finish"):
        event = TraceEvent(run_id="run-test", step=1, event_type=event_type)

        assert event.model_dump()["event_type"] == event_type
