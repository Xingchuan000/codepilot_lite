from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot import __version__
from codepilot.repo.git_utils import sha256_file
from codepilot.repo.models import (
    ArtifactEntry,
    CleanupResult,
    PatchMetadata,
    RepoSafetyResult,
    RepoStateSnapshot,
    RunArtifactManifest,
    to_jsonable,
)


def build_artifact_entry(
    *,
    name: str,
    path: str | Path,
    kind: str,
    run_dir: str | Path,
) -> ArtifactEntry:
    """把真实文件路径压缩成 manifest 里稳定的索引条目。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    artifact_path = Path(path).expanduser().resolve()
    try:
        manifest_path = str(artifact_path.relative_to(run_dir_path))
    except ValueError:
        manifest_path = artifact_path.name
    if not artifact_path.exists():
        return ArtifactEntry(name=name, path=manifest_path, kind=kind, exists=False)
    return ArtifactEntry(
        name=name,
        path=manifest_path,
        kind=kind,
        exists=True,
        size_bytes=artifact_path.stat().st_size,
        sha256=None if name == "artifact_manifest" else sha256_file(artifact_path),
    )


def _redact_path(path: Path, *, redact_absolute_paths: bool) -> str:
    return "[REDACTED_PATH]" if redact_absolute_paths else str(path)


def snapshot_to_manifest_dict(
    snapshot: RepoStateSnapshot | None,
    *,
    redact_absolute_paths: bool = False,
) -> dict[str, Any] | None:
    """把 snapshot 转成 manifest 使用的最小字典，保留脏文件摘要。"""

    if snapshot is None:
        return None
    return {
        "repo_path": _redact_path(snapshot.repo_path, redact_absolute_paths=redact_absolute_paths),
        "head_sha": snapshot.head_sha,
        "branch": snapshot.branch,
        "is_dirty": snapshot.is_dirty,
        "files": [to_jsonable(item) for item in snapshot.files],
        "untracked_count": snapshot.untracked_count,
        "protected_dirty_files": list(snapshot.protected_dirty_files),
    }


def patch_metadata_to_manifest_dict(
    metadata: PatchMetadata | None,
    *,
    run_dir: Path,
    redact_absolute_paths: bool = False,
) -> dict[str, Any] | None:
    """把 patch metadata 转成不会泄露完整 diff 的 manifest 摘要。"""

    if metadata is None:
        return None
    try:
        patch_path = str(metadata.patch_path.relative_to(run_dir))
    except ValueError:
        patch_path = metadata.patch_path.name
    if metadata.generated_from_repo is None:
        generated_from_repo = None
    elif redact_absolute_paths:
        generated_from_repo = "[REDACTED_PATH]"
    else:
        generated_from_repo = str(metadata.generated_from_repo)
    return {
        "patch_path": patch_path,
        "is_empty": metadata.is_empty,
        "size_bytes": metadata.size_bytes,
        "sha256": metadata.sha256,
        "changed_files": list(metadata.changed_files),
        "diff_stat": metadata.diff_stat,
        "base_head_sha": metadata.base_head_sha,
        "effective_head_sha": metadata.effective_head_sha,
        "baseline_dirty": metadata.baseline_dirty,
        "contains_preexisting_changes": metadata.contains_preexisting_changes,
        "generated_from_repo": generated_from_repo,
        "protected_changed_files": list(metadata.protected_changed_files),
        "untracked_files": list(metadata.untracked_files),
        "untracked_files_omitted": list(metadata.untracked_files_omitted),
        "protected_after_files": list(metadata.protected_after_files),
    }


def build_artifact_manifest(
    *,
    run_id: str,
    run_dir: str | Path,
    status: str,
    success: bool | None,
    repo_path: str | Path,
    effective_repo_path: str | Path,
    used_worktree: bool,
    worktree_path: str | Path | None,
    safety_result: RepoSafetyResult,
    before: RepoStateSnapshot | None,
    after: RepoStateSnapshot | None,
    original_after: RepoStateSnapshot | None = None,
    patch_metadata: PatchMetadata | None = None,
    cleanup_result: CleanupResult | None = None,
    artifact_paths: dict[str, Path | None] | None = None,
    redact_absolute_paths: bool = False,
) -> RunArtifactManifest:
    """构造完整 artifact manifest 数据对象。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    artifact_paths = artifact_paths or {}
    redactions_applied = ["absolute_paths"] if redact_absolute_paths else []
    return RunArtifactManifest(
        schema_version="codepilot.artifact_manifest.v1",
        created_at=datetime.now(UTC).isoformat(),
        generator="codepilot",
        generator_version=__version__,
        run_id=run_id,
        status=status,
        success=success,
        repo_path="[REDACTED_PATH]" if redact_absolute_paths else str(Path(repo_path).expanduser().resolve()),
        effective_repo_path="[REDACTED_PATH]"
        if redact_absolute_paths
        else str(Path(effective_repo_path).expanduser().resolve()),
        used_worktree=used_worktree,
        worktree_path=None
        if worktree_path is None
        else ("[REDACTED_PATH]" if redact_absolute_paths else str(Path(worktree_path).expanduser().resolve())),
        safety_decision=safety_result.decision,
        safety_reason=safety_result.reason,
        safety_warnings=list(safety_result.warnings),
        safety_summary={
            "baseline_dirty": safety_result.baseline_dirty,
            "protected_dirty_files": [] if before is None else list(before.protected_dirty_files),
            "used_worktree": used_worktree,
            "contains_preexisting_changes": safety_result.contains_preexisting_changes,
            "cleanup_worktree": safety_result.metadata.get("cleanup_worktree"),
            "protected_changed_files": [] if patch_metadata is None else list(patch_metadata.protected_changed_files),
            "protected_after_files": [] if after is None else list(after.protected_dirty_files),
            "untracked_files": [] if patch_metadata is None else list(patch_metadata.untracked_files),
            "untracked_files_omitted": [] if patch_metadata is None else list(patch_metadata.untracked_files_omitted),
        },
        before=snapshot_to_manifest_dict(before, redact_absolute_paths=redact_absolute_paths),
        after=snapshot_to_manifest_dict(after, redact_absolute_paths=redact_absolute_paths),
        original_after=snapshot_to_manifest_dict(original_after, redact_absolute_paths=redact_absolute_paths),
        patch=patch_metadata_to_manifest_dict(
            patch_metadata,
            run_dir=run_dir_path,
            redact_absolute_paths=redact_absolute_paths,
        ),
        cleanup=None if cleanup_result is None else to_jsonable(cleanup_result),
        artifacts=[
            build_artifact_entry(name=name, path=path or run_dir_path / f"missing-{name}", kind=name, run_dir=run_dir_path)
            for name, path in artifact_paths.items()
        ],
        redactions_applied=redactions_applied,
    )


def write_artifact_manifest(
    manifest: RunArtifactManifest,
    output_path: str | Path,
) -> Path:
    """把 manifest 以 UTF-8 JSON 落盘。"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_artifact_manifest_two_phase(
    manifest: RunArtifactManifest,
    output_path: str | Path,
) -> Path:
    """先写 manifest，再回填 artifact_manifest 自身 exists/size 状态。"""

    path = write_artifact_manifest(manifest, output_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for artifact in payload.get("artifacts", []):
        if artifact.get("name") != "artifact_manifest":
            continue
        artifact["exists"] = True
        artifact["size_bytes"] = path.stat().st_size
        artifact["sha256"] = None
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
