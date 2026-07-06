from __future__ import annotations

"""读取并校验第十三步 auto_pr 产物，为 PR feedback loop 做输入准备。"""

import json
from pathlib import Path
from typing import Any

from codepilot.auto_pr.manifest_loader import load_source_artifact_manifest
from codepilot.pr_assist.manifest_loader import scan_token_like_strings
from codepilot.pr_feedback.models import (
    PRFeedbackInput,
    PRFeedbackManifestInvalidError,
    PRRef,
)
from codepilot.repo.git_utils import sha256_file


def _resolve_inside_run_dir(run_dir: Path, raw_path: str) -> Path:
    """只允许 manifest 引用 run_dir 内部的相对路径。"""

    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise PRFeedbackManifestInvalidError(f"manifest path must be relative: {raw_path}")
    resolved = (run_dir / candidate).resolve()
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise PRFeedbackManifestInvalidError(f"manifest path escapes run_dir: {raw_path}") from exc
    return resolved


def load_auto_pr_manifest(path: str | Path) -> dict[str, Any]:
    """读取 auto_pr_manifest.json，并把明显格式错误转成专用异常。"""

    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    if not manifest_path.is_file():
        raise PRFeedbackManifestInvalidError(f"auto_pr_manifest path is not a file: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PRFeedbackManifestInvalidError(f"auto_pr_manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise PRFeedbackManifestInvalidError("auto_pr_manifest must be a JSON object")
    if data.get("schema_version") != "codepilot.auto_pr_manifest.v1":
        raise PRFeedbackManifestInvalidError("unsupported auto_pr_manifest schema_version")
    if scan_token_like_strings(data):
        raise PRFeedbackManifestInvalidError("token-like string detected in auto_pr_manifest")
    return data


def validate_auto_pr_manifest_for_feedback(manifest: dict[str, Any], run_dir: str | Path) -> list[str]:
    """检查 auto_pr_manifest 是否满足第十四步的最小前置条件。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    errors: list[str] = []
    if manifest.get("schema_version") != "codepilot.auto_pr_manifest.v1":
        errors.append("unsupported schema_version")
    if not manifest.get("run_id"):
        errors.append("missing run_id")
    if not manifest.get("status"):
        errors.append("missing status")
    side_effects = manifest.get("side_effects") or {}
    if side_effects.get("pr_created") is not True and not (manifest.get("repo_slug") and manifest.get("pull_number")):
        errors.append("side_effects.pr_created must be true unless repo_slug and pull_number are provided")
    branch_push_plan = manifest.get("branch_push_plan") or {}
    remote_branch = branch_push_plan.get("remote_branch")
    base_branch = branch_push_plan.get("base_branch") or (manifest.get("pr_request") or {}).get("base_branch")
    if not isinstance(remote_branch, str) or not remote_branch:
        errors.append("missing branch_push_plan.remote_branch")
    elif not remote_branch.startswith("codepilot/"):
        errors.append("branch_push_plan.remote_branch must start with codepilot/")
    elif remote_branch in {"main", "master", base_branch}:
        errors.append("branch_push_plan.remote_branch must not be main, master, or base branch")
    if not branch_push_plan.get("commit_sha"):
        errors.append("missing branch_push_plan.commit_sha")
    pr_result = manifest.get("pr_result") or {}
    if not pr_result.get("url") and not (manifest.get("repo_slug") and manifest.get("pull_number")):
        errors.append("missing pr_result.url and no repo_slug/pull_number override")
    if not pr_result.get("number") and manifest.get("pull_number") is None:
        errors.append("missing pr_result.number and no pull_number override")
    pr_request = manifest.get("pr_request") or {}
    repo = pr_request.get("repo") or {}
    if not repo.get("owner") or not repo.get("repo"):
        if not manifest.get("repo_slug"):
            errors.append("missing pr_request.repo.owner or pr_request.repo.repo")
    if not pr_request.get("base_branch") and not branch_push_plan.get("base_branch"):
        errors.append("missing pr_request.base_branch or branch_push_plan.base_branch")
    source_pr_assist_manifest = manifest.get("source_pr_assist_manifest")
    source_artifact_manifest = manifest.get("source_artifact_manifest")
    if not isinstance(source_pr_assist_manifest, str) or not source_pr_assist_manifest:
        errors.append("missing source_pr_assist_manifest")
    elif Path(source_pr_assist_manifest).is_absolute():
        errors.append("source_pr_assist_manifest must be relative")
    else:
        resolved = _resolve_inside_run_dir(run_dir_path, source_pr_assist_manifest)
        if not resolved.exists():
            errors.append(f"source_pr_assist_manifest missing: {resolved.name}")
    if not isinstance(source_artifact_manifest, str) or not source_artifact_manifest:
        errors.append("missing source_artifact_manifest")
    elif Path(source_artifact_manifest).is_absolute():
        errors.append("source_artifact_manifest must be relative")
    else:
        resolved = _resolve_inside_run_dir(run_dir_path, source_artifact_manifest)
        if not resolved.exists():
            errors.append(f"source_artifact_manifest missing: {resolved.name}")
    return errors


def resolve_pr_ref(
    manifest: dict[str, Any],
    *,
    repo_slug: str | None = None,
    pull_number: int | None = None,
    head_branch: str | None = None,
) -> PRRef:
    """把 auto_pr_manifest 中分散的字段整合成一个 PR 引用。"""

    pr_request = manifest.get("pr_request") or {}
    pr_result = manifest.get("pr_result") or {}
    branch_push_plan = manifest.get("branch_push_plan") or {}
    manifest_repo = pr_request.get("repo") or {}
    repo_parts = repo_slug.split("/", maxsplit=1) if repo_slug else []
    owner = repo_parts[0] if repo_parts else manifest_repo.get("owner")
    repo = repo_parts[1] if repo_parts else manifest_repo.get("repo")
    if not owner or not repo:
        raise PRFeedbackManifestInvalidError("missing repository owner/repo for PR ref")
    number = pull_number if pull_number is not None else pr_result.get("number")
    if not isinstance(number, int):
        raise PRFeedbackManifestInvalidError("missing pull number for PR ref")
    resolved_head_branch = head_branch or branch_push_plan.get("remote_branch") or pr_request.get("head_branch")
    if not isinstance(resolved_head_branch, str) or not resolved_head_branch:
        raise PRFeedbackManifestInvalidError("missing head_branch for PR ref")
    manifest_head_branch = branch_push_plan.get("remote_branch") or pr_request.get("head_branch")
    if head_branch is not None and manifest_head_branch is not None and head_branch != manifest_head_branch:
        raise PRFeedbackManifestInvalidError("head_branch override must match manifest head branch")
    base_branch = pr_request.get("base_branch") or branch_push_plan.get("base_branch")
    if not isinstance(base_branch, str) or not base_branch:
        raise PRFeedbackManifestInvalidError("missing base_branch for PR ref")
    url = pr_result.get("url") or f"https://github.com/{owner}/{repo}/pull/{number}"
    return PRRef(
        owner=str(owner),
        repo=str(repo),
        pull_number=number,
        url=str(url),
        head_branch=str(resolved_head_branch),
        base_branch=str(base_branch),
        head_sha=branch_push_plan.get("commit_sha"),
        base_sha=pr_request.get("base_sha"),
    )


def resolve_feedback_artifact_paths(run_dir: str | Path) -> dict[str, Path]:
    """返回第十四步固定使用的 artifact 位置。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    return {
        "ci_status": run_dir_path / "ci_status.json",
        "review_feedback": run_dir_path / "review_feedback.json",
        "ci_logs_dir": run_dir_path / "ci_logs",
        "ci_feedback_report": run_dir_path / "ci_feedback_report.md",
        "followup_task": run_dir_path / "followup_task.md",
        "pr_update_plan": run_dir_path / "pr_update_plan.md",
        "ci_feedback_manifest": run_dir_path / "ci_feedback_manifest.json",
        "feedback_workflow": run_dir_path / "pr_feedback_workflow.yml",
    }


def load_source_manifests_for_feedback(
    run_dir: str | Path,
    auto_pr_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按照 auto_pr_manifest 的声明回溯读取第十二步与第十一步 manifest。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    source_pr_assist_manifest_raw = auto_pr_manifest.get("source_pr_assist_manifest")
    source_artifact_manifest_raw = auto_pr_manifest.get("source_artifact_manifest")
    if not isinstance(source_pr_assist_manifest_raw, str) or not isinstance(source_artifact_manifest_raw, str):
        raise PRFeedbackManifestInvalidError("missing source manifest paths")
    source_pr_assist_manifest_path = _resolve_inside_run_dir(run_dir_path, source_pr_assist_manifest_raw)
    source_artifact_manifest_path = _resolve_inside_run_dir(run_dir_path, source_artifact_manifest_raw)
    if sha256_file(source_pr_assist_manifest_path) != auto_pr_manifest.get("source_pr_assist_manifest_sha256"):
        raise PRFeedbackManifestInvalidError("source_pr_assist_manifest sha256 mismatch")
    if sha256_file(source_artifact_manifest_path) != auto_pr_manifest.get("source_artifact_manifest_sha256"):
        raise PRFeedbackManifestInvalidError("source_artifact_manifest sha256 mismatch")
    pr_assist_manifest = json.loads(source_pr_assist_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(pr_assist_manifest, dict):
        raise PRFeedbackManifestInvalidError("source_pr_assist_manifest must be a JSON object")
    source_artifact_manifest = load_source_artifact_manifest(run_dir_path, pr_assist_manifest)
    return pr_assist_manifest, source_artifact_manifest


def resolve_pr_feedback_inputs(
    run_dir: str | Path,
    auto_pr_manifest_path: str | Path | None = None,
    **overrides: Any,
) -> PRFeedbackInput:
    """把 run_dir 与 CLI 覆盖项压缩成 workflow 输入对象。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    manifest_path = Path(auto_pr_manifest_path).expanduser().resolve() if auto_pr_manifest_path else run_dir_path / "auto_pr_manifest.json"
    manifest = load_auto_pr_manifest(manifest_path)
    errors = validate_auto_pr_manifest_for_feedback(manifest, run_dir_path)
    if errors:
        raise PRFeedbackManifestInvalidError("; ".join(errors))
    run_id = str(manifest.get("run_id") or run_dir_path.name)
    return PRFeedbackInput(
        run_id=run_id,
        run_dir=run_dir_path,
        auto_pr_manifest_path=manifest_path,
        dry_run=bool(overrides.get("dry_run", True)),
        execute=bool(overrides.get("execute", False)),
        wait_ci=bool(overrides.get("wait_ci", False)),
        include_logs=bool(overrides.get("include_logs", True)),
        include_success_logs=bool(overrides.get("include_success_logs", False)),
        allow_run_agent=bool(overrides.get("allow_run_agent", False)),
        allow_push_update=bool(overrides.get("allow_push_update", False)),
        allow_comment=bool(overrides.get("allow_comment", False)),
        max_feedback_items=int(overrides.get("max_feedback_items", 20)),
        max_log_bytes=int(overrides.get("max_log_bytes", 200_000)),
        max_followup_rounds=int(overrides.get("max_followup_rounds", 1)),
        poll_interval_seconds=int(overrides.get("poll_interval_seconds", 30)),
        timeout_seconds=int(overrides.get("timeout_seconds", 900)),
        token_env=str(overrides.get("token_env", "GITHUB_TOKEN")),
        repo_slug=overrides.get("repo_slug"),
        pull_number=overrides.get("pull_number"),
        head_branch=overrides.get("head_branch"),
        overwrite=bool(overrides.get("overwrite", False)),
    )
