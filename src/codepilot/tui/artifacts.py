from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codepilot.repo.git_utils import sha256_file
from codepilot.tui.models import RunArtifactRef

KNOWN_ARTIFACT_FILES = {
    "trace.jsonl": "trace",
    "report.md": "report_md",
    "report.json": "report_json",
    "changes.patch": "patch",
    "issue.json": "issue_json",
    "pr_summary.md": "pr_summary",
    "pr_body.md": "pr_body",
    "manual_pr_commands.md": "manual_pr_commands",
    "review_checklist.md": "review_checklist",
    "restore_plan.md": "restore_plan",
    "artifact_manifest.json": "artifact_manifest",
    "pr_assist_manifest.json": "pr_assist_manifest",
    "auto_pr_manifest.json": "auto_pr_manifest",
    "pr_feedback_manifest.json": "pr_feedback_manifest",
    "post_pr_manifest.json": "post_pr_manifest",
    "mcp_config_snapshot.json": "mcp_config_snapshot",
    "github_action_template.yml": "github_action_template",
    "github_action_template.yaml": "github_action_template",
}


def safe_artifact_path(run_dir: Path, manifest_path: str) -> tuple[Path | None, str | None]:
    raw = Path(manifest_path)
    if raw.is_absolute():
        return None, "artifact_path_absolute"
    if ".." in raw.parts:
        return None, "artifact_path_traversal"
    resolved = (run_dir / raw).resolve()
    try:
        resolved.relative_to(run_dir.resolve())
    except ValueError:
        return None, "artifact_path_outside_run_dir"
    return resolved, None


def _artifact_warning(*warnings: str) -> tuple[str, ...]:
    return tuple(warnings)


def read_manifest_artifacts(run_dir: str | Path) -> tuple[list[RunArtifactRef], list[str], dict[str, Any]]:
    run_dir_path = Path(run_dir)
    manifest_path = run_dir_path / "artifact_manifest.json"
    if not manifest_path.exists():
        return [], [], {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [], ["bad_artifact_manifest"], {}
    if not isinstance(payload, dict):
        return [], ["bad_artifact_manifest"], {}

    manifest_summary = {
        key: payload.get(key)
        for key in ("run_id", "status", "success", "safety_summary", "patch", "safety_decision", "used_worktree")
    }
    artifacts: list[RunArtifactRef] = []
    for item in payload.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue
        resolved, warning = safe_artifact_path(run_dir_path, path_value)
        if resolved is None:
            artifacts.append(
                RunArtifactRef(
                    kind=str(item.get("kind") or item.get("name") or "other"),
                    path=run_dir_path / path_value,
                    exists=False,
                    verified=False,
                    warnings=_artifact_warning(warning or "artifact_path_invalid"),
                    source="manifest",
                )
            )
            continue
        exists = resolved.exists()
        size_bytes = int(item.get("size_bytes") or 0)
        sha256 = item.get("sha256") if isinstance(item.get("sha256"), str) else None
        warnings: list[str] = []
        verified: bool | None = None
        if not exists:
            warnings.append("artifact_missing")
            verified = False
            size_bytes = 0
            sha256 = None
        else:
            actual_size = resolved.stat().st_size
            if size_bytes and actual_size != size_bytes:
                warnings.append("artifact_size_mismatch")
                verified = False
            elif sha256 is None:
                verified = True
            if sha256 is not None:
                try:
                    actual_sha = sha256_file(resolved)
                except Exception:
                    warnings.append("artifact_sha256_check_failed")
                    actual_sha = None
                if actual_sha is not None and actual_sha != sha256:
                    warnings.append("artifact_sha256_mismatch")
                    verified = False
                elif actual_sha is not None and verified is None:
                    verified = True
        kind = str(item.get("kind") or item.get("name") or "other")
        artifacts.append(
            RunArtifactRef(
                kind=kind,
                path=resolved,
                exists=exists,
                size_bytes=size_bytes if exists else 0,
                sha256=sha256,
                source="manifest",
                verified=verified,
                warnings=tuple(warnings),
            )
        )
    return artifacts, [], manifest_summary


def scan_filesystem_artifacts(run_dir: str | Path) -> list[RunArtifactRef]:
    run_dir_path = Path(run_dir)
    artifacts: list[RunArtifactRef] = []
    skip_names = {".DS_Store", ".gitkeep"}
    for path in sorted(run_dir_path.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        if path.name in skip_names or "__pycache__" in path.parts:
            continue
        kind = KNOWN_ARTIFACT_FILES.get(path.name, "other")
        artifacts.append(
            RunArtifactRef(
                kind=kind,
                path=path,
                exists=True,
                size_bytes=path.stat().st_size,
                sha256=sha256_file(path),
                source="filesystem",
                verified=True,
            )
        )
    return artifacts


def merge_artifact_refs(manifest_refs: list[RunArtifactRef], fs_refs: list[RunArtifactRef]) -> tuple[RunArtifactRef, ...]:
    merged: dict[str, RunArtifactRef] = {}
    for ref in manifest_refs:
        merged[str(ref.path)] = ref
    for ref in fs_refs:
        merged.setdefault(str(ref.path), ref)
    if not any(Path(key).name == "artifact_manifest.json" for key in merged):
        for ref in fs_refs:
            if Path(ref.path).name == "artifact_manifest.json":
                merged[str(ref.path)] = ref
                break
    return tuple(sorted(merged.values(), key=lambda ref: (ref.kind, str(ref.path))))
