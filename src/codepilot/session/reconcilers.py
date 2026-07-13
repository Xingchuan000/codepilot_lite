from __future__ import annotations

import hashlib
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


def reconcile_replace_range(arguments: dict[str, Any]) -> ReconciliationResult:
    repo = Path(arguments["repo"]).resolve()
    path = repo / str(arguments["path"])
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = int(arguments["start_line"]) - 1
    end = int(arguments["end_line"])
    original = "".join(lines[start:end])
    replacement = str(arguments["replacement"])
    original_hash = str(arguments.get("original_hash") or _sha256(original))
    expected_hash = str(arguments.get("expected_hash") or _sha256("".join(lines[:start]) + replacement + "".join(lines[end:])))
    current_hash = _sha256(path.read_text(encoding="utf-8"))
    metadata = {"original_hash": original_hash, "expected_hash": expected_hash, "current_hash": current_hash}
    if current_hash == expected_hash:
        return ReconciliationResult(RecoveryDecision.COMPLETED, "file matches expected replacement", metadata)
    if current_hash == original_hash:
        return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "file still matches the inspected state", metadata)
    return ReconciliationResult(RecoveryDecision.PARTIALLY_COMPLETED, "file differs from both known states", metadata)


def reconcile_apply_patch(arguments: dict[str, Any]) -> ReconciliationResult:
    repo = Path(arguments["repo"]).resolve()
    patch = str(arguments["patch"])
    forward = subprocess.run(["git", "-C", str(repo), "apply", "--check", "-"], input=patch, text=True, capture_output=True, check=False)
    reverse = subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", "-"], input=patch, text=True, capture_output=True, check=False)
    if reverse.returncode == 0:
        return ReconciliationResult(RecoveryDecision.COMPLETED, "patch is already applied", {})
    if forward.returncode == 0:
        return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "patch can still be applied", {})
    return ReconciliationResult(RecoveryDecision.UNKNOWN, "git apply check cannot determine patch state", {"stderr": forward.stderr or reverse.stderr})


def reconcile_run_shell(arguments: dict[str, Any]) -> ReconciliationResult:
    command = str(arguments["command"])
    if _shell_command_is_read_only(command):
        return ReconciliationResult(RecoveryDecision.NOT_EXECUTED, "read-only shell command may be retried", {"command": command})
    return ReconciliationResult(RecoveryDecision.UNKNOWN, "shell command may have side effects", {"command": command})


def _shell_command_is_read_only(command: str) -> bool:
    tokens = command.strip().split()
    return bool(tokens) and tokens[0] in {"cat", "echo", "pwd", "printf", "ls", "find", "grep", "rg", "git"} and not any(token in command for token in [">", "&&", "||", ";", "rm ", "mv ", "cp ", "touch "])


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
