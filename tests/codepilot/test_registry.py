from pathlib import Path

from codepilot.tools.base import DefaultPermission, ToolRisk, ToolSideEffect
from codepilot.tools.registry import TOOL_FUNCTIONS, TOOL_SPECS, call_tool, get_tool_spec, list_tool_specs


def test_registry_contains_expected_tools() -> None:
    assert set(TOOL_SPECS) == {"list_files", "read_file", "search_code", "run_shell", "apply_patch", "replace_range"}
    assert set(TOOL_FUNCTIONS) == {"list_files", "read_file", "search_code", "run_shell", "apply_patch", "replace_range"}
    assert len(list_tool_specs()) == 6


def test_readonly_specs_are_allow() -> None:
    for name in ("list_files", "read_file", "search_code"):
        spec = get_tool_spec(name)
        assert spec.risk == ToolRisk.READ_ONLY
        assert spec.side_effect == ToolSideEffect.NONE
        assert spec.default_permission == DefaultPermission.ALLOW


def test_run_shell_spec_is_ask() -> None:
    spec = get_tool_spec("run_shell")

    assert spec.risk == ToolRisk.SHELL_EXECUTION
    assert spec.side_effect == ToolSideEffect.LOCAL_EXEC
    assert spec.default_permission == DefaultPermission.ASK


def test_edit_tool_specs_are_local_write_ask() -> None:
    for name in ("apply_patch", "replace_range"):
        spec = get_tool_spec(name)
        assert spec.risk == ToolRisk.LOCAL_WRITE
        assert spec.side_effect == ToolSideEffect.LOCAL_WRITE
        assert spec.default_permission == DefaultPermission.ASK


def test_tool_specs_include_parameters() -> None:
    assert "path" in get_tool_spec("read_file").parameters
    assert "query" in get_tool_spec("search_code").parameters
    assert "command" in get_tool_spec("run_shell").parameters
    assert "repo" in get_tool_spec("list_files").parameters
    assert "max_depth" in get_tool_spec("list_files").parameters
    assert "patch" in get_tool_spec("apply_patch").parameters
    assert "path" in get_tool_spec("replace_range").parameters
    assert "start_line" in get_tool_spec("replace_range").parameters
    assert "end_line" in get_tool_spec("replace_range").parameters
    assert "replacement" in get_tool_spec("replace_range").parameters

def test_call_tool_success(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")

    result = call_tool("list_files", repo=tmp_path, path=".")

    assert result.success is True
    assert "hello.txt" in result.output


def test_call_tool_unknown_tool() -> None:
    result = call_tool("unknown", repo=".")

    assert result.success is False
    assert result.error == "Unknown tool: unknown"


def test_call_tool_invalid_arguments_returns_error(tmp_path: Path) -> None:
    result = call_tool("read_file", repo=tmp_path)

    assert result.success is False
    assert "Invalid arguments" in result.error
