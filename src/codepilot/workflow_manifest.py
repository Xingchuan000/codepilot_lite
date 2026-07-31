from __future__ import annotations

"""跨 workflow 复用的源 artifact manifest 读取能力。"""

from pathlib import Path
from typing import Any

from codepilot.auto_pr.models import AutoPRManifestInvalidError
from codepilot.pr_assist.manifest_loader import load_artifact_manifest
from codepilot.repo.git_utils import sha256_file


def load_source_artifact_manifest(run_dir: str | Path, pr_assist_manifest: dict[str, Any]) -> dict[str, Any]:
    """按 pr_assist_manifest 的声明回溯读取第十一步 artifact_manifest。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    raw_path = pr_assist_manifest.get("source_artifact_manifest") or "artifact_manifest.json"
    if not isinstance(raw_path, str):
        raise AutoPRManifestInvalidError("source_artifact_manifest must be a string path")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise AutoPRManifestInvalidError("source_artifact_manifest must be relative")
    source_path = (run_dir_path / candidate).resolve()
    try:
        source_path.relative_to(run_dir_path)
    except ValueError as exc:
        raise AutoPRManifestInvalidError("source_artifact_manifest escapes run_dir") from exc
    if not source_path.exists():
        raise AutoPRManifestInvalidError(f"source artifact manifest missing: {source_path.name}")
    expected_sha = pr_assist_manifest.get("source_artifact_manifest_sha256")
    if not isinstance(expected_sha, str) or not expected_sha:
        raise AutoPRManifestInvalidError("missing source_artifact_manifest_sha256")
    actual_sha = sha256_file(source_path)
    if actual_sha != expected_sha:
        raise AutoPRManifestInvalidError("source artifact manifest sha256 mismatch")
    return load_artifact_manifest(source_path)
