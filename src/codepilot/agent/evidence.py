from __future__ import annotations

import re
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


_READ_ONLY_PHRASES = (
    "不要改代码",
    "不要修改",
    "只读",
    "仅分析",
    "只分析",
    "只解释",
    "无需修改",
    "不要写文件",
    "do not modify",
    "do not change",
    "don't modify",
    "don't change",
    "read only",
    "read-only",
    "analysis only",
    "explain only",
    "without changing code",
)
_MODIFY_PHRASES = (
    "修复",
    "修改",
    "实现",
    "添加",
    "删除",
    "重构",
    "改成",
    "补丁",
    "生成代码",
    "优化代码",
    "解决报错",
    "让它支持",
    "新增功能",
    "fix",
    "modify",
    "implement",
    "add",
    "remove",
    "refactor",
    "patch",
    "change",
    "update code",
    "resolve error",
    "make it support",
    "add feature",
)


def _contains_chinese_phrase(task: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in task for phrase in phrases if any(ord(char) > 127 for char in phrase))


def _contains_english_phrase(task: str, phrases: tuple[str, ...]) -> bool:
    lowered = task.lower()
    for phrase in phrases:
        if any(ord(char) > 127 for char in phrase):
            continue
        pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
        if re.search(pattern, lowered):
            return True
    return False


def classify_task_intent(task: str) -> TaskIntent:
    text = task.strip()
    if not text:
        return "general"
    if _contains_chinese_phrase(text, _READ_ONLY_PHRASES) or _contains_english_phrase(text, _READ_ONLY_PHRASES):
        return "read_only"
    if _contains_chinese_phrase(text, _MODIFY_PHRASES) or _contains_english_phrase(text, _MODIFY_PHRASES):
        return "code_delivery"
    return "general"


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
    observed_changed_files: list[str],
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
    if observed_changed_files:
        # 这里只记录证据判断不依赖观测脏文件的事实，不把它们直接视为本轮修改证据。
        pass
    return EvidenceDecision(
        requires_evidence=requires_evidence,
        tests_required=tests_required,
        diff_required=diff_required,
        reasons=tuple(reasons),
        missing=tuple(missing),
        success_allowed=not missing if requires_evidence else True,
    )
