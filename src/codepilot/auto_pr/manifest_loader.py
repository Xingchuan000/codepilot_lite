from __future__ import annotations

"""读取并校验第十二步 pr_assist 产物，为 auto-pr 做输入准备。"""

import json
from pathlib import Path
from typing import Any

from codepilot.auto_pr.models import AutoPRInput, AutoPRManifestInvalidError
from codepilot.auto_pr.workflow_inputs import validate_head_branch, validate_repo_slug, validate_run_id
from codepilot.pr_assist.manifest_loader import (
    artifact_entries_by_name,
    load_artifact_manifest,
    resolve_artifact_path,
    scan_token_like_strings,
    verify_artifact_integrity,
)
from codepilot.repo.git_utils import sha256_file


def load_pr_assist_manifest(path: str | Path) -> dict[str, Any]:
    """读取 pr_assist_manifest.json，并把格式错误统一映射成专用异常。"""

    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    if not manifest_path.is_file():
        raise AutoPRManifestInvalidError(f"pr_assist_manifest path is not a file: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AutoPRManifestInvalidError(f"pr_assist_manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise AutoPRManifestInvalidError("pr_assist_manifest must be a JSON object")
    if data.get("schema_version") != "codepilot.pr_assist_manifest.v1":
        raise AutoPRManifestInvalidError("unsupported pr_assist_manifest schema_version")
    return data


def generated_artifacts_by_name(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 generated_artifacts 列表压成按 name 索引的字典。"""

    result: dict[str, dict[str, Any]] = {}
    for item in manifest.get("generated_artifacts") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result[item["name"]] = item
    return result


def resolve_generated_artifact_path(run_dir: str | Path, entry: dict[str, Any]) -> Path:
    """只允许 pr_assist 记录 run_dir 内部的相对路径。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise AutoPRManifestInvalidError("generated artifact path must be non-empty string")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise AutoPRManifestInvalidError(f"generated artifact path must be relative: {raw_path}")
    resolved = (run_dir_path / candidate).resolve()
    try:
        resolved.relative_to(run_dir_path)
    except ValueError as exc:
        raise AutoPRManifestInvalidError(f"generated artifact path escapes run_dir: {raw_path}") from exc
    return resolved


def verify_generated_artifact_integrity(path: Path, entry: dict[str, Any]) -> list[str]:
    """校验第十二步生成产物的 exists / size / sha256 是否可信。"""

    errors: list[str] = []
    if bool(entry.get("exists")) and not path.exists():
        return [f"generated artifact missing on disk: {entry.get('name')}"]
    if not path.exists():
        return errors
    expected_size = entry.get("size_bytes")
    if isinstance(expected_size, int) and path.stat().st_size != expected_size:
        errors.append(f"generated artifact size mismatch: {entry.get('name')}")
    expected_sha = entry.get("sha256")
    if entry.get("name") == "pr_assist_manifest":
        return errors
    if isinstance(expected_sha, str) and expected_sha and sha256_file(path) != expected_sha:
        errors.append(f"generated artifact sha256 mismatch: {entry.get('name')}")
    return errors


def load_source_artifact_manifest(run_dir: str | Path, pr_assist_manifest: dict[str, Any]) -> dict[str, Any]:
    """按 pr_assist_manifest 的声明回溯读取第十一步 artifact_manifest。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    raw_path = pr_assist_manifest.get("source_artifact_manifest") or "artifact_manifest.json"
    if not isinstance(raw_path, str):
        raise AutoPRManifestInvalidError("source_artifact_manifest must be a string path")
    source_path = resolve_generated_artifact_path(run_dir_path, {"path": raw_path})
    if not source_path.exists():
        raise AutoPRManifestInvalidError(f"source artifact manifest missing: {source_path.name}")
    expected_sha = pr_assist_manifest.get("source_artifact_manifest_sha256")
    if not isinstance(expected_sha, str) or not expected_sha:
        raise AutoPRManifestInvalidError("missing source_artifact_manifest_sha256")
    actual_sha = sha256_file(source_path)
    if actual_sha != expected_sha:
        raise AutoPRManifestInvalidError("source artifact manifest sha256 mismatch")
    return load_artifact_manifest(source_path)


def resolve_required_auto_pr_artifacts(
    run_dir: str | Path,
    pr_assist_manifest: dict[str, Any],
    source_manifest: dict[str, Any],
) -> dict[str, Path]:
    """解析 auto-pr 需要消费的固定产物路径。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    generated = generated_artifacts_by_name(pr_assist_manifest)
    source_entries = artifact_entries_by_name(source_manifest)
    required_generated = {
        "pr_assist_manifest": {"path": "pr_assist_manifest.json"},
        "pr_body": generated.get("pr_body"),
        "review_checklist": generated.get("review_checklist"),
        "manual_pr_commands": generated.get("manual_pr_commands"),
    }
    required_source = {
        "source_artifact_manifest": source_entries.get("artifact_manifest"),
        "changes_patch": source_entries.get("patch"),
        "pr_summary": source_entries.get("pr_summary"),
        "restore_plan": source_entries.get("restore_plan"),
    }
    resolved: dict[str, Path] = {}
    for name, entry in required_generated.items():
        if entry is None:
            raise AutoPRManifestInvalidError(f"missing required generated artifact: {name}")
        resolved[name] = resolve_generated_artifact_path(run_dir_path, entry)
    for name, entry in required_source.items():
        if entry is None:
            raise AutoPRManifestInvalidError(f"missing required source artifact: {name}")
        resolved[name] = resolve_artifact_path(run_dir_path, entry)
    return resolved


def validate_pr_assist_manifest(manifest: dict[str, Any], run_dir: str | Path, *, allow_empty_pr: bool = False) -> list[str]:
    """按第十三步规则检查 pr_assist_manifest 及其回溯依赖。"""

    errors: list[str] = []
    run_dir_path = Path(run_dir).expanduser().resolve()
    if manifest.get("schema_version") != "codepilot.pr_assist_manifest.v1":
        errors.append("unsupported schema_version")
    if not manifest.get("run_id"):
        errors.append("missing run_id")
    if not manifest.get("source_artifact_manifest_sha256"):
        errors.append("missing source_artifact_manifest_sha256")
    side_effects = manifest.get("side_effects") or {}
    if side_effects.get("push_executed") is not False:
        errors.append("side_effects.push_executed must be false")
    if side_effects.get("pr_created") is not False:
        errors.append("side_effects.pr_created must be false")
    if side_effects.get("github_api_called") is not False:
        errors.append("side_effects.github_api_called must be false")
    generated_list = manifest.get("generated_artifacts")
    if not isinstance(generated_list, list):
        errors.append("generated_artifacts must be a list")
    if scan_token_like_strings(manifest):
        errors.append("token-like string detected in pr_assist_manifest")
    generated = generated_artifacts_by_name(manifest)
    pr_body_entry = generated.get("pr_body")
    if pr_body_entry is None:
        errors.append("missing generated artifact: pr_body")
    else:
        try:
            pr_body_path = resolve_generated_artifact_path(run_dir_path, pr_body_entry)
            errors.extend(verify_generated_artifact_integrity(pr_body_path, pr_body_entry))
            if pr_body_path.exists() and scan_token_like_strings(pr_body_path.read_text(encoding="utf-8", errors="ignore")):
                errors.append("token-like string detected in pr_body.md")
        except AutoPRManifestInvalidError as exc:
            errors.append(str(exc))
    try:
        source_manifest = load_source_artifact_manifest(run_dir_path, manifest)
    except (FileNotFoundError, AutoPRManifestInvalidError) as exc:
        errors.append(str(exc))
        return errors
    source_entries = artifact_entries_by_name(source_manifest)
    patch_entry = source_entries.get("patch")
    if patch_entry is None:
        errors.append("missing source artifact entry: patch")
    else:
        try:
            patch_path = resolve_artifact_path(run_dir_path, patch_entry)
            errors.extend(verify_artifact_integrity(patch_path, patch_entry))
        except Exception as exc:
            errors.append(str(exc))
    patch = source_manifest.get("patch")
    if not isinstance(patch, dict):
        errors.append("missing patch metadata")
    else:
        if patch.get("is_empty") is True and not allow_empty_pr:
            errors.append("empty patch is not allowed")
        if any(str(path).startswith("runs/") or str(path) == "runs" for path in patch.get("changed_files") or []):
            errors.append("patch.changed_files must not include runs/")
        if patch.get("protected_changed_files"):
            errors.append("patch.protected_changed_files must be empty")
    if source_manifest.get("safety_decision") == "deny":
        errors.append("source artifact manifest safety_decision must not be deny")
    safety_gate = manifest.get("safety_gate") or {}
    if not safety_gate.get("status"):
        errors.append("missing pr_assist safety_gate.status")
    elif safety_gate.get("status") != "pass":
        errors.append("pr_assist safety_gate.status must be pass")
    if not manifest.get("branch_name"):
        errors.append("missing branch_name")
    if not manifest.get("commit_sha"):
        errors.append("missing commit_sha")
    for entry in generated.values():
        try:
            resolve_generated_artifact_path(run_dir_path, entry)
        except AutoPRManifestInvalidError as exc:
            errors.append(str(exc))
    return errors


def resolve_auto_pr_inputs(
    run_dir: str | Path,
    *,
    dry_run: bool = True,
    execute: bool = False,
    allow_push: bool = False,
    allow_create_pr: bool = False,
    allow_comment: bool = False,
    allow_empty_pr: bool = False,
    remote_name: str = "origin",
    base_branch: str | None = None,
    head_branch: str | None = None,
    repo_slug: str | None = None,
    token_env: str = "GITHUB_TOKEN",
    draft: bool = True,
    generate_workflow_template: bool = True,
    overwrite: bool = False,
) -> AutoPRInput:
    """从 run_dir 与 pr_assist_manifest 推导 workflow 入口对象。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(run_dir_path)
    manifest_path = run_dir_path / "pr_assist_manifest.json"
    manifest = load_pr_assist_manifest(manifest_path)
    run_id = validate_run_id(str(manifest.get("run_id") or run_dir_path.name))
    return AutoPRInput(
        run_id=run_id,
        run_dir=run_dir_path,
        pr_assist_manifest_path=manifest_path,
        dry_run=dry_run,
        execute=execute,
        allow_push=allow_push,
        allow_create_pr=allow_create_pr,
        allow_comment=allow_comment,
        allow_empty_pr=allow_empty_pr,
        remote_name=remote_name,
        base_branch=base_branch,
        head_branch=validate_head_branch(head_branch, run_id=run_id),
        repo_slug=validate_repo_slug(repo_slug),
        token_env=token_env,
        draft=draft,
        generate_workflow_template=generate_workflow_template,
        overwrite=overwrite,
    )
