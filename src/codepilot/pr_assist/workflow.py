from __future__ import annotations

"""第十二步 pr-assist 主编排逻辑。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.pr_assist.branch import prepare_local_branch, sanitize_branch_name
from codepilot.pr_assist.checklist import render_review_checklist, write_review_checklist
from codepilot.pr_assist.commands import render_manual_pr_commands, write_manual_pr_commands
from codepilot.pr_assist.commit import prepare_commit, render_commit_message
from codepilot.pr_assist.github_action import write_github_action_template
from codepilot.pr_assist.manifest_loader import (
    build_safety_gate,
    load_artifact_manifest,
    resolve_required_artifacts,
    scan_token_like_strings,
    validate_required_artifacts,
)
from codepilot.pr_assist.models import (
    PRAssistResult,
    PRAssistSafetyGate,
    PRAssistStatus,
    to_pr_assist_jsonable,
)
from codepilot.pr_assist.pr_body import build_pr_body_data, read_json_if_exists, write_pr_body
from codepilot.repo.git_utils import sha256_file


PR_ASSIST_ARTIFACT_NAMES = [
    "pr_body.md",
    "manual_pr_commands.md",
    "review_checklist.md",
    "github_action_template.yml",
    "pr_assist_manifest.json",
]


def _ensure_can_write(run_dir: Path, *, overwrite: bool) -> None:
    """只管理第十二步自己的产物，避免误删第十一步已有文件。"""

    existing = [run_dir / name for name in PR_ASSIST_ARTIFACT_NAMES if (run_dir / name).exists()]
    if existing and not overwrite:
        raise FileExistsError("PR assist artifacts already exist: " + ", ".join(str(path) for path in existing))
    if overwrite:
        for path in existing:
            path.unlink()


def _read_text(path: Path | None) -> str | None:
    """读取可选文本文件。"""

    if path is None or not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _artifact_record(name: str, path: Path, *, run_dir: Path) -> dict[str, Any]:
    """把新生成的 artifact 写成 manifest 可记录的轻量索引。"""

    try:
        display_path = str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        display_path = path.name
    return {
        "name": name,
        "path": display_path,
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "sha256": sha256_file(path) if path.exists() else None,
    }


def _assert_no_token_like_strings_in_path(path: Path) -> None:
    """每写完一个新文本 artifact 就重新扫描一次，防止把敏感内容落盘。"""

    if scan_token_like_strings(path.read_text(encoding="utf-8", errors="ignore")):
        raise ValueError(f"token-like string detected in generated artifact: {path.name}")


def write_pr_assist_manifest(
    *,
    output_path: str | Path,
    run_id: str,
    run_dir: Path,
    source_manifest_path: Path,
    safety_gate: PRAssistSafetyGate,
    generated_artifacts: dict[str, Path | None],
    status: PRAssistStatus,
    include_gh_pr_command: bool,
    branch_name: str | None,
    commit_sha: str | None,
    warnings: list[str],
    overwrite: bool = False,
) -> Path:
    """写 pr_assist_manifest.json，并固定记录所有副作用开关均未越界。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    artifact_items = [
        _artifact_record(name, artifact_path, run_dir=run_dir)
        for name, artifact_path in generated_artifacts.items()
        if artifact_path is not None
    ]
    payload = {
        "schema_version": "codepilot.pr_assist_manifest.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "status": status,
        "source_artifact_manifest": "artifact_manifest.json",
        "source_artifact_manifest_sha256": sha256_file(source_manifest_path),
        "safety_gate": to_pr_assist_jsonable(safety_gate),
        "generated_artifacts": artifact_items,
        "manual_commands": {
            "include_gh_pr_command": include_gh_pr_command,
        },
        "side_effects": {
            "branch_prepared": branch_name is not None,
            "commit_prepared": commit_sha is not None,
            "push_executed": False,
            "pr_created": False,
            "github_api_called": False,
        },
        "branch_name": branch_name,
        "commit_sha": commit_sha,
        "warnings": warnings,
    }
    if scan_token_like_strings(payload):
        raise ValueError("token-like string detected in pr_assist_manifest payload")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    final_payload = json.loads(path.read_text(encoding="utf-8"))
    final_payload["generated_artifacts"].append(
        {
            "name": "pr_assist_manifest",
            "path": "pr_assist_manifest.json",
            "exists": True,
            "size_bytes": path.stat().st_size,
            "sha256": None,
        }
    )
    if scan_token_like_strings(final_payload):
        raise ValueError("token-like string detected in pr_assist_manifest payload")
    path.write_text(json.dumps(final_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _assert_no_token_like_strings_in_path(path)
    return path


def run_pr_assist(
    *,
    run_dir: str | Path,
    strict_safety: bool = True,
    redact_absolute_paths: bool = True,
    include_gh_pr_command: bool = False,
    generate_github_action_template: bool = True,
    prepare_branch: bool = False,
    branch_prefix: str = "codepilot",
    commit: bool = False,
    commit_message_file: str | Path | None = None,
    overwrite: bool = False,
) -> PRAssistResult:
    """从第十一步 artifacts 生成第十二步人工 PR 辅助材料。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(f"run_dir does not exist: {run_dir_path}")
    _ensure_can_write(run_dir_path, overwrite=overwrite)

    manifest_path = run_dir_path / "artifact_manifest.json"
    manifest = load_artifact_manifest(manifest_path)
    run_id = str(manifest.get("run_id") or run_dir_path.name)

    validation_errors = validate_required_artifacts(run_dir_path, manifest)
    safety_gate = build_safety_gate(manifest)
    warnings: list[str] = []
    if validation_errors:
        minimal_manifest = write_pr_assist_manifest(
            output_path=run_dir_path / "pr_assist_manifest.json",
            run_id=run_id,
            run_dir=run_dir_path,
            source_manifest_path=manifest_path,
            safety_gate=safety_gate,
            generated_artifacts={},
            status="manifest_invalid",
            include_gh_pr_command=include_gh_pr_command,
            branch_name=None,
            commit_sha=None,
            warnings=validation_errors,
            overwrite=True,
        )
        return PRAssistResult(
            run_id=run_id,
            run_dir=run_dir_path,
            status="manifest_invalid",
            safety_gate=safety_gate,
            pr_assist_manifest_path=minimal_manifest,
            warnings=validation_errors,
        )

    artifacts = resolve_required_artifacts(run_dir_path, manifest)
    report_json = read_json_if_exists(artifacts.get("report_json"))
    issue_json = read_json_if_exists(artifacts.get("issue_json"))
    pr_summary_text = _read_text(artifacts.get("pr_summary"))

    pr_body_data = build_pr_body_data(
        manifest=manifest,
        report_json=report_json,
        issue_json=issue_json,
        pr_summary_text=pr_summary_text,
        run_dir=run_dir_path,
        artifacts=artifacts,
        safety_gate=safety_gate,
    )
    pr_body_path = write_pr_body(
        pr_body_data,
        run_dir_path / "pr_body.md",
        safety_gate=safety_gate,
        overwrite=True,
    )
    _assert_no_token_like_strings_in_path(pr_body_path)

    checklist_text = render_review_checklist(manifest=manifest, pr_body_data=pr_body_data, safety_gate=safety_gate)
    checklist_path = write_review_checklist(checklist_text, run_dir_path / "review_checklist.md", overwrite=True)
    _assert_no_token_like_strings_in_path(checklist_path)

    branch_name = sanitize_branch_name(run_id, prefix=branch_prefix)
    manual_plan = render_manual_pr_commands(
        run_id=run_id,
        repo_path=manifest.get("repo_path"),
        effective_repo_path=manifest.get("effective_repo_path"),
        run_dir=run_dir_path,
        patch_path=artifacts.get("patch"),
        branch_name=branch_name,
        safety_gate=safety_gate,
        include_gh_pr_command=include_gh_pr_command,
        redact_absolute_paths=redact_absolute_paths,
    )
    manual_commands_path = write_manual_pr_commands(manual_plan, run_dir_path / "manual_pr_commands.md", overwrite=True)
    _assert_no_token_like_strings_in_path(manual_commands_path)
    warnings.extend(manual_plan.warnings)

    github_action_template_path = None
    if generate_github_action_template:
        github_action_template_path = write_github_action_template(
            run_dir_path / "github_action_template.yml",
            overwrite=True,
        )
        _assert_no_token_like_strings_in_path(github_action_template_path)

    status: PRAssistStatus = "generated"
    prepared_branch_name: str | None = None
    commit_sha: str | None = None

    if safety_gate.status == "fail":
        if prepare_branch or commit:
            warnings.append("Safety gate failed. Branch/commit preparation skipped.")
        if strict_safety:
            status = "blocked_by_safety"
        else:
            warnings.append("strict_safety is disabled. Generated review-only materials; side effects remain disabled.")
            status = "generated"
    else:
        repo_for_side_effect = manifest.get("effective_repo_path") or manifest.get("repo_path")
        if repo_for_side_effect == "[REDACTED_PATH]" or not repo_for_side_effect:
            if prepare_branch or commit:
                warnings.append("Repo path is redacted or missing. Branch/commit preparation skipped.")
        elif prepare_branch:
            try:
                prepared_branch_name = prepare_local_branch(repo_for_side_effect, branch_name=branch_name)
                status = "branch_prepared"
            except Exception as exc:
                warnings.append(f"branch preparation failed: {exc}")
                status = "branch_failed"
        if commit and repo_for_side_effect and repo_for_side_effect != "[REDACTED_PATH]":
            if safety_gate.status != "pass":
                warnings.append(f"commit preparation skipped because safety_gate={safety_gate.status}.")
                status = "commit_failed"
            elif prepare_branch and prepared_branch_name is None:
                warnings.append("commit preparation skipped because branch preparation failed.")
            else:
                try:
                    if commit_message_file is not None:
                        message = Path(commit_message_file).read_text(encoding="utf-8")
                    else:
                        message = render_commit_message(
                            issue_title=pr_body_data.title,
                            changed_files=pr_body_data.changed_files,
                            tests_summary="; ".join(pr_body_data.tests),
                            run_id=run_id,
                        )
                    commit_sha = prepare_commit(
                        repo_for_side_effect,
                        message=message,
                        changed_files=pr_body_data.changed_files,
                        run_id=run_id,
                    )
                    status = "commit_prepared"
                except Exception as exc:
                    warnings.append(f"commit preparation failed: {exc}")
                    status = "commit_failed"

    generated_artifacts = {
        "pr_body": pr_body_path,
        "manual_pr_commands": manual_commands_path,
        "review_checklist": checklist_path,
        "github_action_template": github_action_template_path,
    }
    pr_assist_manifest_path = write_pr_assist_manifest(
        output_path=run_dir_path / "pr_assist_manifest.json",
        run_id=run_id,
        run_dir=run_dir_path,
        source_manifest_path=manifest_path,
        safety_gate=safety_gate,
        generated_artifacts=generated_artifacts,
        status=status,
        include_gh_pr_command=include_gh_pr_command,
        branch_name=prepared_branch_name,
        commit_sha=commit_sha,
        warnings=warnings,
        overwrite=True,
    )

    return PRAssistResult(
        run_id=run_id,
        run_dir=run_dir_path,
        status=status,
        safety_gate=safety_gate,
        pr_body_path=pr_body_path,
        manual_commands_path=manual_commands_path,
        review_checklist_path=checklist_path,
        github_action_template_path=github_action_template_path,
        pr_assist_manifest_path=pr_assist_manifest_path,
        branch_name=prepared_branch_name,
        commit_sha=commit_sha,
        warnings=warnings,
    )
