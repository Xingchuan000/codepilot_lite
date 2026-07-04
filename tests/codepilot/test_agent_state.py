from pathlib import Path

import pytest

from codepilot.agent.actions import AgentFinishAction
from codepilot.agent.state import create_initial_state, looks_like_pytest_command, mark_finished_from_action, update_state_from_route_result
from codepilot.router.actions import ToolRouteResult
from codepilot.tools.base import ToolResult


def test_create_initial_state_sets_task_repo_and_max_steps(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)

    assert state.task == "demo"
    assert state.repo == tmp_path.resolve()
    assert state.max_steps == 3


def test_create_initial_state_rejects_non_positive_max_steps(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_initial_state("demo", tmp_path, max_steps=0)


def test_update_state_from_run_tests_metadata(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    result = ToolRouteResult(
        action_id="a1",
        tool_name="run_tests",
        success=False,
        result=ToolResult(
            success=False,
            metadata={"status": "failed", "command": "pytest", "failed_tests": ["tests/test_a.py::test_a"]},
        ),
    )

    update_state_from_route_result(state, result)

    assert state.last_test_status == "failed"
    assert state.last_test_command == "pytest"
    assert state.last_failed_tests == ["tests/test_a.py::test_a"]


def test_update_state_from_git_status_and_git_diff(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    git_status_result = ToolRouteResult(
        action_id="a1",
        tool_name="git_status",
        success=True,
        result=ToolResult(success=True, metadata={"changed_files": ["a.py", "b.py"]}),
    )
    git_diff_result = ToolRouteResult(
        action_id="a2",
        tool_name="git_diff",
        success=True,
        result=ToolResult(success=True, metadata={"path": "a.py"}),
    )

    update_state_from_route_result(state, git_status_result)
    update_state_from_route_result(state, git_diff_result)

    assert state.changed_files == ["a.py", "b.py"]
    assert state.diff_checked is True
    assert state.diff_paths_checked == ["a.py"]


def test_update_state_from_replace_range_and_apply_patch(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    replace_result = ToolRouteResult(
        action_id="a1",
        tool_name="replace_range",
        success=True,
        result=ToolResult(success=True, metadata={"changed": True, "path": "src/a.py"}),
    )
    patch_result = ToolRouteResult(
        action_id="a2",
        tool_name="apply_patch",
        success=True,
        result=ToolResult(success=True, metadata={"touched_paths": ["src/a.py", "src/b.py"]}),
    )

    update_state_from_route_result(state, replace_result)
    update_state_from_route_result(state, patch_result)

    assert state.changed_files == ["src/a.py", "src/b.py"]


def test_update_state_counts_policy_violations(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    denied = ToolRouteResult(
        action_id="a1",
        tool_name="replace_range",
        success=False,
        result=ToolResult(success=False, metadata={"policy_decision": "deny"}),
    )

    update_state_from_route_result(state, denied)

    assert state.policy_violations == 1


def test_update_state_from_run_shell_pytest_command_marks_passed(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    result = ToolRouteResult(
        action_id="a1",
        tool_name="run_shell",
        success=True,
        result=ToolResult(success=True, metadata={"command": "python -m pytest tests/", "returncode": 0}),
    )

    update_state_from_route_result(state, result)

    assert state.last_test_status == "passed"
    assert state.last_test_command == "python -m pytest tests/"
    assert state.last_failed_tests == []


def test_update_state_from_run_shell_non_pytest_command_is_ignored(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    result = ToolRouteResult(
        action_id="a1",
        tool_name="run_shell",
        success=True,
        result=ToolResult(success=True, metadata={"command": "echo pytest", "returncode": 0}),
    )

    update_state_from_route_result(state, result)

    assert state.last_test_status is None
    assert state.last_test_command is None


def test_looks_like_pytest_command_handles_common_forms() -> None:
    assert looks_like_pytest_command("pytest")
    assert looks_like_pytest_command("pytest -q")
    assert looks_like_pytest_command("python -m pytest")
    assert looks_like_pytest_command("python3 -m pytest tests/")
    assert looks_like_pytest_command("/absolute/path/to/python -m pytest tests/")
    assert not looks_like_pytest_command("echo pytest")
    assert not looks_like_pytest_command("cat pytest.ini")
    assert not looks_like_pytest_command("python script.py pytest")


def test_mark_finished_from_action_updates_state(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    action = AgentFinishAction(type="finish", status="success", summary="done", changed_files=["a.py"])

    mark_finished_from_action(state, action)

    assert state.finished is True
    assert state.final_status == "success"
    assert state.final_summary == "done"
    assert state.changed_files == ["a.py"]
