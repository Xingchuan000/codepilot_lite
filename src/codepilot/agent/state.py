from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codepilot.agent.actions import AgentFinishAction
from codepilot.llm.types import ChatMessage
from codepilot.router.actions import ToolRouteResult


@dataclass
class AgentState:
    """MinimalAgentLoop 在内存里维护的最小运行状态。"""

    task: str
    repo: Path
    messages: list[ChatMessage] = field(default_factory=list)
    step: int = 0
    max_steps: int = 12
    finished: bool = False
    final_status: str | None = None
    final_summary: str | None = None
    last_tool_name: str | None = None
    last_tool_success: bool | None = None
    last_test_status: str | None = None
    last_test_command: str | None = None
    last_failed_tests: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    diff_checked: bool = False
    diff_paths_checked: list[str] = field(default_factory=list)
    policy_violations: int = 0
    tool_calls: list[str] = field(default_factory=list)


def _append_unique(items: list[str], value: str) -> None:
    """保持原顺序的去重追加。"""

    if value not in items:
        items.append(value)


def create_initial_state(task: str, repo: str | Path, *, max_steps: int) -> AgentState:
    """创建 loop 初始状态。"""

    if max_steps <= 0:
        raise ValueError("max_steps must be greater than 0")
    return AgentState(task=task, repo=Path(repo).resolve(), max_steps=max_steps)


def update_state_from_route_result(state: AgentState, route_result: ToolRouteResult) -> None:
    """把一次工具调用结果回写到 AgentState。"""

    metadata = route_result.result.metadata
    state.last_tool_name = route_result.tool_name
    state.last_tool_success = route_result.success
    state.tool_calls.append(route_result.tool_name)
    if metadata.get("policy_decision") == "deny" or metadata.get("policy_violation") is True:
        state.policy_violations += 1
    if route_result.tool_name == "run_tests":
        if isinstance(metadata.get("status"), str):
            state.last_test_status = metadata["status"]
        if isinstance(metadata.get("command"), str):
            state.last_test_command = metadata["command"]
        if isinstance(metadata.get("failed_tests"), list):
            state.last_failed_tests = [str(item) for item in metadata["failed_tests"]]
    if route_result.tool_name == "git_status":
        if isinstance(metadata.get("changed_files"), list):
            for path in metadata["changed_files"]:
                if isinstance(path, str):
                    _append_unique(state.changed_files, path)
    if route_result.tool_name == "git_diff" and route_result.success:
        state.diff_checked = True
        if isinstance(metadata.get("path"), str) and metadata["path"]:
            _append_unique(state.diff_paths_checked, metadata["path"])
    if route_result.tool_name == "replace_range":
        if metadata.get("changed") is True and isinstance(metadata.get("path"), str):
            _append_unique(state.changed_files, metadata["path"])
    if route_result.tool_name == "apply_patch":
        touched_paths = metadata.get("touched_paths")
        if isinstance(touched_paths, list):
            for path in touched_paths:
                if isinstance(path, str):
                    _append_unique(state.changed_files, path)


def mark_finished_from_action(state: AgentState, action: AgentFinishAction) -> None:
    """把 finish 动作写回状态。"""

    state.finished = True
    state.final_status = action.status
    state.final_summary = action.summary
    for path in action.changed_files:
        _append_unique(state.changed_files, path)
