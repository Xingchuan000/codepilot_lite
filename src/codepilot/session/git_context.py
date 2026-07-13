from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitContext:
    """项目当前的 Git 分支信息；本阶段不记录 HEAD commit。"""

    is_git_repo: bool
    branch: str | None


def read_git_context(project_path: Path) -> GitContext:
    """只读 Git 分支；非 Git 目录按普通项目处理。"""

    result = subprocess.run(
        ["git", "-C", str(project_path), "rev-parse", "--abbrev-ref", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return GitContext(is_git_repo=False, branch=None)
    branch = result.stdout.strip()
    return GitContext(is_git_repo=True, branch=branch or None)
