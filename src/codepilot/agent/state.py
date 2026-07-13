from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from codepilot.agent.actions import AgentFinishAction
from codepilot.agent.evidence import (
    AssistantStopReason,
    CompletionKind,
    EvidenceDecision,
    EvidenceSnapshot,
    TaskIntent,
    evaluate_evidence,
    shell_command_may_write,
)
from codepilot.llm.types import ChatMessage
from codepilot.router.actions import ToolRouteResult


@dataclass
class AgentState:
    """MinimalAgentLoop 在内存里维护的最小运行状态。"""

    task: str
    repo: Path
    task_intent: TaskIntent = "general"
    task_requires_code_delivery: bool = False
    requires_evidence: bool = False
    evidence_reasons: list[str] = field(default_factory=list)
    write_attempted: bool = False
    write_executed: bool = False
    written_files: list[str] = field(default_factory=list)
    observed_changed_files: list[str] = field(default_factory=list)
    claimed_changed_files: list[str] = field(default_factory=list)
    tests_required: bool = False
    diff_required: bool = False
    missing_evidence: list[str] = field(default_factory=list)
    assistant_stop_reason: AssistantStopReason | None = None
    completion_kind: CompletionKind | None = None
    delivery_kind: str | None = None
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


def evidence_snapshot(state: AgentState) -> EvidenceSnapshot:
    """从可变 AgentState 复制证据字段，并形成唯一的不可变快照。

    这里是 AgentState 到运行结果证据字段的唯一复制位置。使用 tuple 会切断快照与
    State 内部 list 的引用关系，避免任务结束后状态变化意外改写已发布的结果。
    """

    return EvidenceSnapshot(
        requires_evidence=state.requires_evidence,
        reasons=tuple(state.evidence_reasons),
        write_attempted=state.write_attempted,
        write_executed=state.write_executed,
        written_files=tuple(state.written_files),
        observed_changed_files=tuple(state.observed_changed_files),
        claimed_changed_files=tuple(state.claimed_changed_files),
        tests_required=state.tests_required,
        diff_required=state.diff_required,
        diff_checked=state.diff_checked,
        missing=tuple(state.missing_evidence),
    )


def _append_unique(items: list[str], value: str) -> None:
    """保持原顺序的去重追加。"""

    if value not in items:
        items.append(value)


def _register_written_paths(state: AgentState, paths: list[str]) -> None:
    """把真实写入过的路径同时登记到 written_files 和 changed_files。"""

    for path in paths:
        _append_unique(state.written_files, path)
        _append_unique(state.changed_files, path)


def _collect_changed_paths(metadata: dict[str, object], *, include_path_when_changed: bool = False) -> list[str]:
    """从工具结果里提取真实发生变化的文件路径。

    这里只接受字符串路径，并保持顺序去重，避免把其它字段误判成写入证据。
    """

    changed_paths: list[str] = []
    for key in ("changed_files", "touched_paths"):
        value = metadata.get(key)
        if isinstance(value, list):
            for path in value:
                if isinstance(path, str):
                    _append_unique(changed_paths, path)
    if include_path_when_changed and metadata.get("changed") is True and isinstance(metadata.get("path"), str):
        _append_unique(changed_paths, metadata["path"])
    return changed_paths


def looks_like_pytest_command(command: str) -> bool:
    """用很小的启发式判断命令是否是在跑 pytest。"""

    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    first_token = Path(tokens[0]).name
    if first_token == "pytest":
        return True
    if len(tokens) >= 3 and first_token.startswith("python") and tokens[1] == "-m" and tokens[2] == "pytest":
        return True
    return False


def create_initial_state(task: str, repo: str | Path, *, max_steps: int) -> AgentState:
    """创建 loop 初始状态。"""

    if max_steps <= 0:
        raise ValueError("max_steps must be greater than 0")
    state = AgentState(
        task=task,
        repo=Path(repo).resolve(),
        # 初始状态不再依赖关键词分类器，后续完全根据真实工具副作用动态升级证据门禁。
        task_intent="general",
        task_requires_code_delivery=False,
        requires_evidence=False,
        evidence_reasons=[],
        max_steps=max_steps,
    )
    refresh_evidence_state(state)
    return state


def register_tool_attempt(
    state: AgentState,
    *,
    tool_name: str,
    side_effect: str | None,
    arguments: dict[str, object],
) -> None:
    """在真正调用路由前登记“试图执行过什么工具”。"""

    if side_effect == "local_write":
        state.write_attempted = True
    if tool_name == "run_shell" and isinstance(arguments.get("command"), str) and shell_command_may_write(arguments["command"]):
        state.write_attempted = True
    if state.write_attempted and f"write_attempted:{tool_name}" not in state.evidence_reasons:
        state.evidence_reasons.append(f"write_attempted:{tool_name}")


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
    if route_result.tool_name == "run_shell" and isinstance(metadata.get("command"), str) and looks_like_pytest_command(metadata["command"]):
        if route_result.success and metadata.get("returncode") == 0:
            state.last_test_status = "passed"
            state.last_test_command = metadata["command"]
            state.last_failed_tests = []
        elif isinstance(metadata.get("returncode"), int):
            state.last_test_status = "failed"
            state.last_test_command = metadata["command"]
    if route_result.tool_name == "git_status":
        if isinstance(metadata.get("changed_files"), list):
            for path in metadata["changed_files"]:
                if isinstance(path, str):
                    _append_unique(state.observed_changed_files, path)
                    _append_unique(state.changed_files, path)
    if route_result.tool_name == "git_diff" and route_result.success:
        state.diff_checked = True
        if isinstance(metadata.get("path"), str) and metadata["path"]:
            _append_unique(state.diff_paths_checked, metadata["path"])
    changed_paths = _collect_changed_paths(metadata, include_path_when_changed=route_result.tool_name == "replace_range")
    if route_result.tool_name == "replace_range":
        # replace_range 只有在确实发生内容变化时，才登记为真实写入。
        if metadata.get("changed") is True and changed_paths:
            _register_written_paths(state, changed_paths)
    if route_result.tool_name == "apply_patch":
        # apply_patch 以 touched_paths 为准，dry_run 或空路径都不算有效写入证据。
        if route_result.success and metadata.get("dry_run") is not True and changed_paths:
            _register_written_paths(state, changed_paths)
    if (
        metadata.get("executed") is True
        and metadata.get("dry_run") is not True
        and route_result.tool_name not in {"replace_range", "apply_patch"}
    ):
        if metadata.get("side_effect") == "local_write" or route_result.tool_name == "run_shell" and isinstance(metadata.get("command"), str) and shell_command_may_write(metadata["command"]):
            state.write_executed = True
    if route_result.tool_name in {"replace_range", "apply_patch"} and route_result.success and metadata.get("dry_run") is not True and changed_paths:
        state.write_executed = True
    if (
        route_result.tool_name not in {"replace_range", "apply_patch"}
        and metadata.get("side_effect") == "local_write"
        and metadata.get("executed") is True
        and metadata.get("dry_run") is not True
        and changed_paths
    ):
        _register_written_paths(state, changed_paths)
        state.write_executed = True
    if route_result.tool_name == "run_shell" and route_result.success and isinstance(metadata.get("command"), str) and shell_command_may_write(metadata["command"]):
        state.write_executed = True


def register_finish_claim(state: AgentState, action: AgentFinishAction) -> None:
    """只记录模型声称的 changed_files，不把它当作真实写入证据。"""

    for path in action.changed_files:
        _append_unique(state.claimed_changed_files, path)


def refresh_evidence_state(state: AgentState) -> EvidenceDecision:
    """基于当前状态重新计算 evidence gate。"""

    preserved_reasons = [reason for reason in state.evidence_reasons if reason.startswith("write_attempted:")]
    decision = evaluate_evidence(
        task_requires_code_delivery=state.task_requires_code_delivery,
        write_attempted=state.write_attempted,
        write_executed=state.write_executed,
        written_files=state.written_files,
        claimed_changed_files=state.claimed_changed_files,
        last_test_status=state.last_test_status,
        diff_checked=state.diff_checked,
    )
    state.requires_evidence = decision.requires_evidence
    state.tests_required = decision.tests_required
    state.diff_required = decision.diff_required
    state.evidence_reasons = list(dict.fromkeys([*decision.reasons, *preserved_reasons]))
    state.missing_evidence = list(decision.missing)
    return decision


def mark_finished_from_action(
    state: AgentState,
    action: AgentFinishAction,
    *,
    effective_status: str | None = None,
    completion_kind: CompletionKind | None = None,
    delivery_kind: str | None = None,
) -> None:
    """把 finish 动作写回状态。"""

    state.finished = True
    state.final_status = effective_status or action.status
    state.final_summary = action.summary
    state.assistant_stop_reason = "structured_finish"
    state.completion_kind = completion_kind
    if state.delivery_kind == "code_change" or delivery_kind == "code_change" or action.delivery_kind == "code_change":
        state.delivery_kind = "code_change"
    else:
        state.delivery_kind = delivery_kind or action.delivery_kind
    register_finish_claim(state, action)
