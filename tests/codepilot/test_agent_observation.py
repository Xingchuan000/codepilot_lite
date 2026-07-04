from codepilot.agent.actions import AgentActionParseError
from codepilot.agent.observation import format_observation, format_parse_error_observation
from codepilot.router.actions import ToolRouteResult
from codepilot.tools.base import ToolResult


def test_format_observation_for_failed_run_tests() -> None:
    route_result = ToolRouteResult(
        action_id="a1",
        tool_name="run_tests",
        success=False,
        result=ToolResult(
            success=False,
            output="full output",
            output_summary="Tests failed: 1 failed.",
            error="Test command failed with returncode 1.",
            metadata={"status": "failed", "failed_tests": ["tests/test_a.py::test_a"], "returncode": 1},
        ),
    )

    observation = format_observation(route_result)

    assert "Tool: run_tests" in observation
    assert "failed_tests" in observation
    assert "returncode" in observation


def test_format_observation_for_passed_run_tests() -> None:
    route_result = ToolRouteResult(
        action_id="a1",
        tool_name="run_tests",
        success=True,
        result=ToolResult(success=True, output_summary="Tests passed.", metadata={"status": "passed"}),
    )

    assert "status: passed" in format_observation(route_result)


def test_format_observation_for_git_status() -> None:
    route_result = ToolRouteResult(
        action_id="a1",
        tool_name="git_status",
        success=True,
        result=ToolResult(success=True, output_summary="changed", metadata={"changed_files": ["a.py"], "clean": False}),
    )

    observation = format_observation(route_result)

    assert "changed_files" in observation
    assert "clean: False" in observation


def test_format_observation_truncates_long_output() -> None:
    route_result = ToolRouteResult(
        action_id="a1",
        tool_name="git_diff",
        success=True,
        result=ToolResult(success=True, output="x" * 5000),
    )

    observation = format_observation(route_result, max_output_chars=100)

    assert "Output preview:" in observation
    assert observation.endswith("... truncated")


def test_format_observation_for_policy_deny() -> None:
    route_result = ToolRouteResult(
        action_id="a1",
        tool_name="replace_range",
        success=False,
        result=ToolResult(
            success=False,
            error="denied",
            metadata={"executed": False, "policy_decision": "deny", "policy_reason": "blocked"},
        ),
    )

    observation = format_observation(route_result)

    assert "Executed: false" in observation
    assert "Policy: deny" in observation
    assert "policy_reason: blocked" in observation


def test_format_observation_keeps_only_whitelisted_metadata() -> None:
    route_result = ToolRouteResult(
        action_id="a1",
        tool_name="read_file",
        success=True,
        result=ToolResult(success=True, metadata={"path": "a.py", "secret_field": "nope"}),
    )

    observation = format_observation(route_result)

    assert "path: a.py" in observation
    assert "secret_field" not in observation


def test_format_parse_error_observation() -> None:
    observation = format_parse_error_observation(AgentActionParseError("bad json"))

    assert "Action parse failed" in observation
    assert "tool_name" in observation
    assert "arguments" in observation


def test_format_parse_error_observation_mentions_non_standard_fields() -> None:
    error = AgentActionParseError(
        "Missing required field after normalization: tool_name.",
        raw_action={"type": "tool_call", "tool": "list_files", "parameters": {}},
        normalized_action={"type": "tool_call", "arguments": {}},
        normalization_metadata={
            "normalization_applied": True,
            "normalized_fields": {"parameters": "arguments"},
            "non_standard_fields": ["tool", "parameters"],
            "conflicts": [],
        },
    )

    observation = format_parse_error_observation(error)

    assert "tool" in observation
    assert "parameters" in observation
    assert "tool_name" in observation
    assert "arguments" in observation
    assert '{"type":"tool_call"' in observation
