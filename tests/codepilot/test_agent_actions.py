import pytest

from codepilot.agent.actions import (
    AgentActionParseError,
    AgentFinishAction,
    AgentToolCallAction,
    agent_action_to_trace_input,
    parse_agent_action,
    parse_agent_action_with_metadata,
)


def test_parse_tool_call_action() -> None:
    action = parse_agent_action('{"type":"tool_call","tool_name":"read_file","arguments":{"path":"a.py"}}')

    assert isinstance(action, AgentToolCallAction)
    assert action.tool_name == "read_file"


def test_parse_finish_action() -> None:
    action = parse_agent_action('{"type":"finish","status":"success","summary":"done"}')

    assert isinstance(action, AgentFinishAction)
    assert action.status == "success"


def test_parse_standard_action_has_no_normalization_metadata() -> None:
    parsed = parse_agent_action_with_metadata('{"type":"tool_call","tool_name":"read_file","arguments":{"path":"a.py"}}')

    assert isinstance(parsed.action, AgentToolCallAction)
    assert parsed.normalization_metadata["normalization_applied"] is False
    assert parsed.normalization_metadata["normalized_fields"] == {}


@pytest.mark.parametrize(
    ("text", "expected_tool", "expected_arguments", "expected_fields"),
    [
        (
            '{"action":"list_files","parameters":{"path":"."}}',
            "list_files",
            {"path": "."},
            {"action": "tool_name", "parameters": "arguments"},
        ),
        (
            '{"type":"tool_call","tool":"list_files","parameters":{"path":"."}}',
            "list_files",
            {"path": "."},
            {"tool": "tool_name", "parameters": "arguments"},
        ),
        (
            '{"name":"read_file","input":{"path":"src/calc.py"}}',
            "read_file",
            {"path": "src/calc.py"},
            {"name": "tool_name", "input": "arguments"},
        ),
        (
            '{"function_name":"run_tests","args":{"command":"pytest -q"}}',
            "run_tests",
            {"command": "pytest -q"},
            {"function_name": "tool_name", "args": "arguments"},
        ),
    ],
)
def test_parse_agent_action_normalizes_common_aliases(
    text: str, expected_tool: str, expected_arguments: dict[str, object], expected_fields: dict[str, str]
) -> None:
    parsed = parse_agent_action_with_metadata(text)

    assert isinstance(parsed.action, AgentToolCallAction)
    assert parsed.action.tool_name == expected_tool
    assert parsed.action.arguments == expected_arguments
    assert parsed.normalization_metadata["normalization_applied"] is True
    for src, dst in expected_fields.items():
        assert parsed.normalization_metadata["normalized_fields"][src] == dst


def test_standard_fields_win_over_alias_fields() -> None:
    parsed = parse_agent_action_with_metadata(
        '{"type":"tool_call","tool_name":"read_file","tool":"list_files","arguments":{"path":"a.py"},"parameters":{"path":"."}}'
    )

    assert parsed.action.tool_name == "read_file"
    assert parsed.action.arguments == {"path": "a.py"}
    assert parsed.normalization_metadata["conflicts"]
    assert "tool_name/tool" in parsed.normalization_metadata["conflicts"]
    assert "arguments/parameters" in parsed.normalization_metadata["conflicts"]


def test_unknown_action_alias_is_rejected() -> None:
    with pytest.raises(AgentActionParseError, match="Unknown action alias"):
        parse_agent_action('{"action":"do_magic","parameters":{}}')


def test_type_final_without_status_returns_clear_error() -> None:
    with pytest.raises(AgentActionParseError, match="status"):
        parse_agent_action('{"type":"final","summary":"done"}')


def test_action_finish_with_required_fields_normalizes_to_finish() -> None:
    parsed = parse_agent_action_with_metadata('{"action":"finish","status":"success","summary":"done"}')

    assert isinstance(parsed.action, AgentFinishAction)
    assert parsed.action.type == "finish"
    assert parsed.normalization_metadata["normalization_applied"] is True


def test_parse_agent_action_extracts_fenced_json_object() -> None:
    action = parse_agent_action('```json\n{"type":"finish","status":"success","summary":"done"}\n```')

    assert isinstance(action, AgentFinishAction)


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
