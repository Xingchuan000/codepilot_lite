from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any



class RecoveryDecision(str, Enum):
    COMPLETED = "completed"
    NOT_EXECUTED = "not_executed"
    PARTIALLY_COMPLETED = "partially_completed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReconciliationResult:
    decision: RecoveryDecision
    detail: str
    metadata: dict[str, Any]


def reconcile_read_only(**_: Any) -> ReconciliationResult:
    return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "read-only tool has no side effect", {})


def reconcile_replace_range(recovery_token: dict[str, Any]) -> ReconciliationResult:
    """只比较执行前持久化的全文件 hash，不从当前文件重建预期状态。"""

    try:
        path = Path(str(recovery_token["path"]))
        current_hash = _sha256_bytes(path.read_bytes())
        pre_hash = str(recovery_token["pre_file_sha256"])
        expected_hash = str(recovery_token["expected_file_sha256"])
    except Exception as exc:
        return ReconciliationResult(RecoveryDecision.UNKNOWN, "replace_range token or file cannot be inspected", {"error": str(exc)})
    metadata = {"pre_file_sha256": pre_hash, "expected_file_sha256": expected_hash, "current_file_sha256": current_hash}
    if current_hash == expected_hash:
        return ReconciliationResult(RecoveryDecision.COMPLETED, "file matches expected replacement", metadata)
    if current_hash == pre_hash:
        return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "file still matches the pre-execution state", metadata)
    return ReconciliationResult(RecoveryDecision.PARTIALLY_COMPLETED, "file differs from both durable states", metadata)


def reconcile_apply_patch(arguments: dict[str, Any], recovery_token: dict[str, Any]) -> ReconciliationResult:
    try:
        repo = Path(str(recovery_token["repo"])).resolve()
        patch = str(arguments["patch"])
        if _sha256_bytes(patch.encode("utf-8")) != recovery_token.get("patch_sha256"):
            return ReconciliationResult(RecoveryDecision.UNKNOWN, "patch does not match durable recovery token", {})
        if recovery_token.get("forward_check_before") is not True:
            return ReconciliationResult(RecoveryDecision.UNKNOWN, "patch was not applicable before execution", {})
        baseline_head = recovery_token.get("baseline_head")
        if baseline_head is not None:
            current_head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=False,
            )
            if current_head.returncode != 0 or current_head.stdout.strip() != baseline_head:
                return ReconciliationResult(RecoveryDecision.UNKNOWN, "repository HEAD differs from durable baseline", {})
        forward = subprocess.run(["git", "-C", str(repo), "apply", "--check", "-"], input=patch, text=True, capture_output=True, check=False)
        reverse = subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", "-"], input=patch, text=True, capture_output=True, check=False)
    except Exception as exc:
        return ReconciliationResult(RecoveryDecision.UNKNOWN, "git apply reconciliation failed", {"error": str(exc)})
    if reverse.returncode == 0:
        return ReconciliationResult(RecoveryDecision.COMPLETED, "patch is already applied", {})
    if forward.returncode == 0:
        return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "patch can still be applied", {})
    return ReconciliationResult(RecoveryDecision.UNKNOWN, "git apply check cannot determine patch state", {"stderr": forward.stderr or reverse.stderr})


def reconcile_run_shell(arguments: dict[str, Any], recovery_token: dict[str, Any]) -> ReconciliationResult:
    command = str(arguments["command"])
    if _sha256_bytes(command.encode("utf-8")) != recovery_token.get("command_sha256"):
        return ReconciliationResult(RecoveryDecision.UNKNOWN, "shell command does not match durable recovery token", {})
    if recovery_token.get("auto_retry_allowed") is True:
        return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "read-only shell command may be retried", {"command": command})
    return ReconciliationResult(RecoveryDecision.UNKNOWN, "shell command may have side effects", {"command": command})


def shell_command_is_read_only(command: str) -> bool:
    """严格识别可自动重试的只读 Shell；复合 shell 语法一律不自动重试。"""

    if any(token in command for token in (">", "<", "|", ";", "&&", "||", "`", "$(", "\n", "\r")):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    if tokens[0] in {"pwd", "ls", "cat", "grep", "rg"}:
        return True
    return len(tokens) >= 2 and tokens[0] == "git" and tokens[1] in {"status", "diff", "log", "show"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
