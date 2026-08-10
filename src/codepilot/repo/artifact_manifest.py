from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codepilot.repo.git_utils import sha256_file


class ArtifactManifestError(ValueError):
    """A current artifact manifest violates the repository artifact contract."""


def load_verified_manifest(
    run_dir: str | Path,
    relative_path: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Read one manifest declared relative to a run directory and verify its digest."""

    root = Path(run_dir).expanduser().resolve()
    path = Path(relative_path)
    if path.is_absolute():
        raise ArtifactManifestError("artifact manifest path must be relative")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArtifactManifestError("artifact manifest path escapes run_dir") from exc
    if not resolved.is_file():
        raise ArtifactManifestError(f"artifact manifest missing: {resolved.name}")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise ArtifactManifestError("missing artifact manifest sha256")
    if sha256_file(resolved) != expected_sha256:
        raise ArtifactManifestError("artifact manifest sha256 mismatch")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactManifestError("artifact manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactManifestError("artifact manifest must be a JSON object")
    return value
