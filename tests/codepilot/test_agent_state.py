from pathlib import Path

import pytest

from codepilot.agent.actions import AgentFinishAction
from codepilot.agent.state import (
    create_initial_state,
    looks_like_pytest_command,
    mark_finished_from_action,
    refresh_evidence_state,
    register_finish_claim,
    register_tool_attempt,
    update_state_from_route_result,
)
from codepilot.router.actions import ToolRouteResult
from codepilot.tools.base import ToolResult


def test_create_initial_state_sets_task_repo_and_max_steps(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)

    assert state.task == "demo"
    assert state.repo == tmp_path.resolve()
    assert state.max_steps == 3
    assert state.task_intent == "general"


def test_create_initial_state_stays_general_before_writes(tmp_path: Path) -> None:
    state = create_initial_state("修复 add bug", tmp_path, max_steps=3)

    assert state.task_intent == "general"
    assert state.requires_evidence is False
    assert state.evidence_reasons == []


def test_create_initial_state_ignores_read_only_keyword(tmp_path: Path) -> None:
    state = create_initial_state("不要改代码，只分析失败原因", tmp_path, max_steps=3)

    assert state.task_intent == "general"
    assert state.requires_evidence is False


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
    assert state.observed_changed_files == ["a.py", "b.py"]
    assert state.diff_checked is True
    assert state.diff_paths_checked == ["a.py"]


def test_update_state_from_replace_range_and_apply_patch(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    replace_result = ToolRouteResult(
        action_id="a1",
        tool_name="replace_range",
        success=True,
        result=ToolResult(success=True, metadata={"changed": True, "path": "src/a.py", "executed": True, "side_effect": "local_write"}),
    )
    patch_result = ToolRouteResult(
        action_id="a2",
        tool_name="apply_patch",
        success=True,
        result=ToolResult(success=True, metadata={"touched_paths": ["src/a.py", "src/b.py"], "executed": True, "side_effect": "local_write"}),
    )

    update_state_from_route_result(state, replace_result)
    update_state_from_route_result(state, patch_result)

    assert state.changed_files == ["src/a.py", "src/b.py"]
    assert state.written_files == ["src/a.py", "src/b.py"]
    assert state.write_executed is True


def test_update_state_from_local_write_tool_registers_written_paths(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    result = ToolRouteResult(
        action_id="a1",
        tool_name="mcp.filesystem.write_file",
        success=True,
        result=ToolResult(
            success=True,
            metadata={"changed_files": ["src/a.py"], "executed": True, "side_effect": "local_write"},
        ),
    )

    update_state_from_route_result(state, result)

    assert state.write_executed is True
    assert state.written_files == ["src/a.py"]
    assert state.changed_files == ["src/a.py"]


def test_noop_replace_range_does_not_count_as_executed_change(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    result = ToolRouteResult(
        action_id="a1",
        tool_name="replace_range",
        success=True,
        result=ToolResult(success=True, metadata={"changed": False, "path": "src/a.py", "executed": True, "side_effect": "local_write"}),
    )

    update_state_from_route_result(state, result)

    assert state.write_executed is False
    assert state.written_files == []


def test_denied_local_write_does_not_set_write_executed(tmp_path: Path) -> None:
    state = create_initial_state("修复 add bug", tmp_path, max_steps=3)
    register_tool_attempt(state, tool_name="replace_range", side_effect="local_write", arguments={})
    denied = ToolRouteResult(
        action_id="a1",
        tool_name="replace_range",
        success=False,
        result=ToolResult(success=False, metadata={"side_effect": "local_write", "executed": False, "policy_decision": "deny"}),
    )

    update_state_from_route_result(state, denied)

    assert state.write_attempted is True
    assert state.write_executed is False


def test_git_status_only_tracks_observed_files(tmp_path: Path) -> None:
    state = create_initial_state("修复 add bug", tmp_path, max_steps=3)
    result = ToolRouteResult(
        action_id="a1",
        tool_name="git_status",
        success=True,
        result=ToolResult(success=True, metadata={"changed_files": ["a.py"]}),
    )

    update_state_from_route_result(state, result)

    assert state.observed_changed_files == ["a.py"]
    assert state.written_files == []


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


def test_register_tool_attempt_marks_write_attempt(tmp_path: Path) -> None:
    state = create_initial_state("修复 add bug", tmp_path, max_steps=3)

    register_tool_attempt(state, tool_name="replace_range", side_effect="local_write", arguments={})

    assert state.write_attempted is True
    assert state.evidence_reasons == ["write_attempted:replace_range"]


def test_register_tool_attempt_marks_shell_write(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)

    register_tool_attempt(state, tool_name="run_shell", side_effect="none", arguments={"command": "echo x > a.txt"})

    assert state.write_attempted is True


def test_register_tool_attempt_ignores_read_only_tools(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)

    register_tool_attempt(state, tool_name="git_status", side_effect="none", arguments={})

    assert state.write_attempted is False


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
    assert state.claimed_changed_files == ["a.py"]
    assert state.changed_files == []
    assert state.assistant_stop_reason == "structured_finish"


def test_register_finish_claim_only_tracks_declared_files(tmp_path: Path) -> None:
    state = create_initial_state("修复 add bug", tmp_path, max_steps=3)
    action = AgentFinishAction(type="finish", status="success", summary="done", changed_files=["a.py"])

    register_finish_claim(state, action)

    assert state.claimed_changed_files == ["a.py"]
    assert state.changed_files == []
    assert state.written_files == []


def test_refresh_evidence_state_sets_missing_evidence(tmp_path: Path) -> None:
    state = create_initial_state("修复 add bug", tmp_path, max_steps=3)
    state.task_requires_code_delivery = True

    decision = refresh_evidence_state(state)

    assert decision.missing == ("missing_write_execution", "missing_changed_files")
    assert state.missing_evidence == ["missing_write_execution", "missing_changed_files"]


def test_write_executed_without_changed_paths_keeps_missing_changed_files(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=3)
    state.task_requires_code_delivery = True
    state.write_attempted = True
    state.write_executed = True
    decision = refresh_evidence_state(state)

    assert decision.requires_evidence is True
    assert "missing_changed_files" in decision.missing


def test_mark_finished_from_action_can_use_effective_status(tmp_path: Path) -> None:
    state = create_initial_state("hello", tmp_path, max_steps=3)
    action = AgentFinishAction(type="finish", status="success", summary="done")

    mark_finished_from_action(state, action, effective_status="message_complete", completion_kind="message_complete")

    assert state.final_status == "message_complete"
    assert state.completion_kind == "message_complete"
