from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from codepilot.session.reconcilers import (
    RecoveryDecision,
    reconcile_apply_patch,
    reconcile_replace_range,
    reconcile_run_shell,
    shell_command_is_read_only,
)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_replace_range_uses_only_durable_full_file_hashes(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    before = b"one\ntwo\nthree\n"
    after = b"one\nchanged\nthree\n"
    token = {"path": str(path), "pre_file_sha256": _hash(before), "expected_file_sha256": _hash(after)}

    path.write_bytes(before)
    assert reconcile_replace_range(token).decision == RecoveryDecision.NOT_EXECUTED
    path.write_bytes(after)
    assert reconcile_replace_range(token).decision == RecoveryDecision.COMPLETED
    path.write_text("third state\n", encoding="utf-8")
    assert reconcile_replace_range(token).decision == RecoveryDecision.PARTIALLY_COMPLETED


def test_apply_patch_reconciles_forward_reverse_and_unknown(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    path = tmp_path / "a.txt"
    path.write_text("old\n", encoding="utf-8")
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"
    token = {"repo": str(tmp_path), "patch_sha256": _hash(patch.encode("utf-8")), "forward_check_before": True, "baseline_head": None}

    assert reconcile_apply_patch({"patch": patch}, token).decision == RecoveryDecision.NOT_EXECUTED
    subprocess.run(["git", "apply", "-"], cwd=tmp_path, input=patch, text=True, check=True)
    assert reconcile_apply_patch({"patch": patch}, token).decision == RecoveryDecision.COMPLETED
    assert reconcile_apply_patch({"patch": patch + "x"}, token).decision == RecoveryDecision.UNKNOWN


def test_shell_recovery_uses_strict_allowlist_and_token(tmp_path: Path) -> None:
    assert shell_command_is_read_only("git status") is True
    assert shell_command_is_read_only("git diff --stat") is True
    assert shell_command_is_read_only("git commit -m x") is False
    assert shell_command_is_read_only("git checkout main") is False
    assert shell_command_is_read_only("git reset --hard") is False
    assert shell_command_is_read_only("git clean -fd") is False
    assert shell_command_is_read_only("find . -delete") is False
    assert shell_command_is_read_only("cat a | sh") is False

    command = "git status"
    token = {"command_sha256": _hash(command.encode("utf-8")), "auto_retry_allowed": True}
    assert reconcile_run_shell({"command": command}, token).decision == RecoveryDecision.NOT_EXECUTED
    assert reconcile_run_shell({"command": "git reset --hard"}, token).decision == RecoveryDecision.UNKNOWN
