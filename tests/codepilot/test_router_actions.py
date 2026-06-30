import json

import pytest
from pydantic import ValidationError

from codepilot.router.actions import ToolAction, ToolRouteResult
from codepilot.tools.base import ToolResult


def test_tool_action_defaults() -> None:
    action = ToolAction(
        tool_name="list_files",
        arguments={"repo": ".", "path": "src"},
    )

    assert action.action_id.startswith("act-")
    assert len(action.action_id) == len("act-") + 12
    assert action.tool_name == "list_files"
    assert action.arguments["path"] == "src"
    assert action.reason is None
    assert action.metadata == {}


def test_tool_action_strips_tool_name() -> None:
    action = ToolAction(tool_name="  list_files  ")

    assert action.tool_name == "list_files"


def test_tool_action_rejects_empty_tool_name() -> None:
    with pytest.raises(ValidationError):
        ToolAction(tool_name="  ", arguments={})


def test_tool_action_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ToolAction(tool_name="list_files", arguments={}, extra_field=True)


def test_tool_action_rejects_non_dict_arguments() -> None:
    with pytest.raises(ValidationError):
        ToolAction(tool_name="list_files", arguments=["repo", "."])


def test_tool_action_can_validate_dict() -> None:
    action = ToolAction.model_validate(
        {
            "tool_name": "read_file",
            "arguments": {"repo": ".", "path": "README.md"},
            "reason": "Inspect README.",
            "metadata": {"source": "test"},
        }
    )

    assert action.tool_name == "read_file"
    assert action.arguments["path"] == "README.md"
    assert action.reason == "Inspect README."
    assert action.metadata["source"] == "test"


def test_tool_route_result_serializes_nested_tool_result() -> None:
    routed = ToolRouteResult(
        action_id="act-test",
        tool_name="list_files",
        success=True,
        result=ToolResult(success=True, output="a.txt", output_summary="Listed 1 file."),
        trace_path="runs/run-test/trace.jsonl",
        metadata={"run_id": "run-test"},
    )

    data = json.loads(routed.model_dump_json())

    assert data["action_id"] == "act-test"
    assert data["tool_name"] == "list_files"
    assert data["success"] is True
    assert data["result"]["success"] is True
    assert data["result"]["output"] == "a.txt"
    assert data["trace_path"] == "runs/run-test/trace.jsonl"
    assert data["metadata"]["run_id"] == "run-test"
