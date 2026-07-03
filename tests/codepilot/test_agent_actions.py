import pytest

from codepilot.agent.actions import (
    AgentActionParseError,
    AgentFinishAction,
    AgentToolCallAction,
    agent_action_to_trace_input,
    parse_agent_action,
)


def test_parse_tool_call_action() -> None:
    action = parse_agent_action('{"type":"tool_call","tool_name":"read_file","arguments":{"path":"a.py"}}')

    assert isinstance(action, AgentToolCallAction)
    assert action.tool_name == "read_file"


def test_parse_finish_action() -> None:
    action = parse_agent_action('{"type":"finish","status":"success","summary":"done"}')

    assert isinstance(action, AgentFinishAction)
    assert action.status == "success"


@pytest.mark.parametrize(
    ("text",),
    [
        ("not json",),
        ("[]",),
        ("```json\n{}\n```",),
        ('{"status":"success","summary":"done"}',),
        ('{"type":"unknown"}',),
        ('{"type":"tool_call","arguments":{}}',),
        ('{"type":"tool_call","tool_name":"read_file","arguments":[]}',),
        ('{"type":"finish","status":"success"}',),
    ],
)
def test_parse_agent_action_rejects_invalid_input(text: str) -> None:
    with pytest.raises(AgentActionParseError):
        parse_agent_action(text)


def test_agent_action_to_trace_input_truncates_patch_and_redacts_secret_fields() -> None:
    action = AgentToolCallAction(
        type="tool_call",
        tool_name="apply_patch",
        arguments={
            "patch": "x" * 1200,
            "replacement": "y" * 1200,
            "api_key": "secret",
            "nested": {"token": "hidden"},
        },
    )

    trace_input = agent_action_to_trace_input(action)

    assert trace_input["arguments"]["patch"].endswith("... truncated")
    assert trace_input["arguments"]["replacement"].endswith("... truncated")
    assert trace_input["arguments"]["api_key"] == "[REDACTED]"
    assert trace_input["arguments"]["nested"]["token"] == "[REDACTED]"
