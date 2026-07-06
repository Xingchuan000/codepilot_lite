from __future__ import annotations

"""构建 auto-pr 阶段的远端副作用安全门。"""

from pathlib import PurePosixPath
from typing import Any

from codepilot.auto_pr.models import AutoPRSafetyError, AutoPRSafetyGate


FORBIDDEN_CHANGED_PATH_PATTERNS = [
    ".env",
    ".env.*",
    "secrets/**",
    ".ssh/**",
    ".github/workflows/**",
]
PROTECTED_BRANCH_NAMES = {"main", "master", "HEAD"}


def _matches_forbidden_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return any(pure.match(pattern) for pattern in FORBIDDEN_CHANGED_PATH_PATTERNS)


def build_auto_pr_safety_gate(
    *,
    pr_assist_manifest: dict[str, Any],
    source_artifact_manifest: dict[str, Any],
    allow_empty_pr: bool = False,
) -> AutoPRSafetyGate:
    """综合第十一步与第十二步信息，给出远端副作用是否允许的结论。"""

    reasons: list[str] = []
    warnings: list[str] = []
    pr_gate = pr_assist_manifest.get("safety_gate") or {}
    side_effects = pr_assist_manifest.get("side_effects") or {}
    patch = source_artifact_manifest.get("patch") or {}
    safety_summary = source_artifact_manifest.get("safety_summary") or {}
    status = source_artifact_manifest.get("status")
    safety_decision = str(source_artifact_manifest.get("safety_decision") or "")

    if pr_gate.get("status") != "pass":
        reasons.append("pr_assist safety gate must be pass")
    if safety_decision not in {"allow", "pass"}:
        reasons.append("source artifact manifest safety_decision must allow remote side effects")
    if status in {"repo_safety_denied", "protected_patch_path_denied", "protected_after_path_denied"}:
        reasons.append(str(status))
    if not isinstance(patch, dict):
        reasons.append("patch metadata missing")
    else:
        if patch.get("is_empty") is True and not allow_empty_pr:
            reasons.append("empty patch is not allowed")
        if patch.get("protected_changed_files"):
            reasons.append("patch.protected_changed_files must be empty")
        if patch.get("protected_after_files"):
            reasons.append("patch.protected_after_files must be empty")
        for path in patch.get("changed_files") or []:
            text = str(path)
            if text == "runs" or text.startswith("runs/"):
                reasons.append("patch.changed_files must not include runs/")
            if _matches_forbidden_path(text):
                reasons.append(f"forbidden changed path: {text}")
    branch_name = pr_assist_manifest.get("branch_name")
    if not branch_name:
        reasons.append("branch_name missing")
    elif not str(branch_name).startswith("codepilot/"):
        reasons.append("branch_name must start with codepilot/")
    elif str(branch_name) in PROTECTED_BRANCH_NAMES:
        reasons.append("branch_name points to a protected branch")
    if not pr_assist_manifest.get("commit_sha"):
        reasons.append("commit_sha missing")
    if side_effects.get("branch_prepared") is not True:
        reasons.append("side_effects.branch_prepared must be true")
    if side_effects.get("commit_prepared") is not True:
        reasons.append("side_effects.commit_prepared must be true")
    if side_effects.get("push_executed") is not False:
        reasons.append("side_effects.push_executed must be false")
    if side_effects.get("pr_created") is not False:
        reasons.append("side_effects.pr_created must be false")
    if side_effects.get("github_api_called") is not False:
        reasons.append("side_effects.github_api_called must be false")
    if safety_summary.get("baseline_dirty") is True:
        warnings.append("baseline_dirty=true")
    if reasons:
        return AutoPRSafetyGate(status="fail", reasons=reasons, warnings=warnings)
    if warnings:
        return AutoPRSafetyGate(status="warn", reasons=reasons, warnings=warnings)
    return AutoPRSafetyGate(status="pass", reasons=reasons, warnings=warnings)


def assert_remote_side_effect_allowed(
    *,
    safety_gate: AutoPRSafetyGate,
    execute: bool,
    allow_push: bool,
    allow_create_pr: bool,
) -> None:
    """在真正执行 push / create PR 之前做 fail-closed 断言。"""

    if not execute:
        return
    if safety_gate.status != "pass":
        raise AutoPRSafetyError(f"remote side effect blocked: safety_gate={safety_gate.status}")
    if not allow_push:
        raise AutoPRSafetyError("remote side effect blocked: allow_push is false")
    if allow_create_pr and not allow_push:
        raise AutoPRSafetyError("allow_create_pr requires allow_push")


def summarize_safety_gate(gate: AutoPRSafetyGate) -> str:
    """把安全门压缩成适合展示在 plan / comment 中的短句。"""

    details = gate.reasons or gate.warnings
    return gate.status if not details else f"{gate.status}: {details[0]}"
