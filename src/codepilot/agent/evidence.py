from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Literal


TaskIntent = Literal["code_delivery", "read_only", "general"]
CompletionKind = Literal[
    "message_complete",
    "task_success",
    "task_partial",
    "task_incomplete",
    "task_failed",
    "cancelled",
    "runtime_failure",
]
AssistantStopReason = Literal["natural_reply", "structured_finish", "max_steps", "cancelled", "llm_error", "llm_exhausted"]


@dataclass(frozen=True)
class EvidenceDecision:
    requires_evidence: bool
    tests_required: bool
    diff_required: bool
    reasons: tuple[str, ...]
    missing: tuple[str, ...]
    success_allowed: bool


@dataclass(frozen=True)
class EvidenceSnapshot:
    """一次运行结束时不可变的证据快照。

    AgentState 使用 list 维护运行中的可变状态；结束结果、Trace 和 TUI 则需要稳定的数据。
    因此快照内部统一使用 tuple，只在生成 JSON 兼容 payload 时转换为 list。
    """

    requires_evidence: bool
    reasons: tuple[str, ...]
    write_attempted: bool
    write_executed: bool
    written_files: tuple[str, ...]
    observed_changed_files: tuple[str, ...]
    claimed_changed_files: tuple[str, ...]
    tests_required: bool
    diff_required: bool
    diff_checked: bool
    missing: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        """按现有 Trace/TUI 字段契约生成可序列化 payload。"""

        return {
            "requires_evidence": self.requires_evidence,
            "evidence_reasons": list(self.reasons),
            "write_attempted": self.write_attempted,
            "write_executed": self.write_executed,
            "written_files": list(self.written_files),
            "observed_changed_files": list(self.observed_changed_files),
            "claimed_changed_files": list(self.claimed_changed_files),
            "tests_required": self.tests_required,
            "diff_required": self.diff_required,
            "diff_checked": self.diff_checked,
            "missing_evidence": list(self.missing),
        }


def shell_command_may_write(command: str) -> bool:
    text = command.strip()
    if not text:
        return False
    if ">>" in text or ">" in text:
        return True
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return False
    if tokens[0] in {"tee", "touch", "mkdir", "rm", "mv", "cp"}:
        return True
    if len(tokens) >= 2 and tokens[0] == "sed" and "-i" in tokens[1:]:
        return True
    if len(tokens) >= 2 and tokens[0] == "perl" and "-i" in tokens[1:]:
        return True
    if len(tokens) >= 2 and tokens[0] == "git" and tokens[1] in {"add", "commit", "checkout", "switch", "reset", "clean"}:
        return True
    if len(tokens) >= 2 and tokens[0] in {"pip", "npm", "pnpm"} and tokens[1] == "install":
        return True
    if len(tokens) >= 2 and tokens[0] == "yarn" and tokens[1] == "add":
        return True
    if len(tokens) >= 4 and tokens[0] == "python" and tokens[1] == "-m" and tokens[2] == "pip" and tokens[3] == "install":
        return True
    return False


def evaluate_evidence(
    *,
    task_requires_code_delivery: bool,
    write_attempted: bool,
    write_executed: bool,
    written_files: list[str],
    claimed_changed_files: list[str],
    last_test_status: str | None,
    diff_checked: bool,
) -> EvidenceDecision:
    has_real_write_evidence = bool(write_executed or written_files)
    requires_evidence = bool(
        task_requires_code_delivery
        or write_attempted
        or has_real_write_evidence
    )
    tests_required = requires_evidence and has_real_write_evidence
    diff_required = tests_required
    reasons: list[str] = []
    missing: list[str] = []
    if requires_evidence:
        if task_requires_code_delivery:
            reasons.append("task_requires_code_delivery")
        if write_attempted:
            reasons.append("write_attempted")
        if write_executed:
            reasons.append("write_executed")
        if written_files:
            reasons.append("written_files")
    if requires_evidence and not write_executed and not written_files:
        missing.append("missing_write_execution")
    if requires_evidence and not written_files:
        missing.append("missing_changed_files")
    if tests_required and last_test_status != "passed":
        missing.append("missing_passed_tests")
    if diff_required and not diff_checked:
        missing.append("missing_diff_check")
    return EvidenceDecision(
        requires_evidence=requires_evidence,
        tests_required=tests_required,
        diff_required=diff_required,
        reasons=tuple(reasons),
        missing=tuple(missing),
        success_allowed=not missing if requires_evidence else True,
    )
