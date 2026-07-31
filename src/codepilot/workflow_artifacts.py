from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from codepilot.repo.git_utils import sha256_file


def prepare_owned_artifacts(
    run_dir: Path,
    names: Iterable[str],
    *,
    overwrite: bool,
    label: str,
) -> None:
    """只检查或删除调用方明确声明拥有的 artifact。"""

    existing = [run_dir / name for name in names if (run_dir / name).exists()]
    if existing and not overwrite:
        joined = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"{label} artifacts already exist: {joined}")
    if overwrite:
        for path in existing:
            path.unlink()


def build_artifact_record(
    name: str,
    path: Path,
    *,
    run_dir: Path,
) -> dict[str, Any]:
    """生成 manifest 使用的稳定 artifact 索引。"""

    try:
        display_path = str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        display_path = path.name

    exists = path.exists()
    return {
        "name": name,
        "path": display_path,
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
        "sha256": sha256_file(path) if exists else None,
    }
