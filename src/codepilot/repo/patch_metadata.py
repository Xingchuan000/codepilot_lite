from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from codepilot.repo.git_utils import GitCommandError, run_git, sha256_file
from codepilot.repo.models import PatchMetadata
from codepilot.repo.safety import find_protected_paths, normalize_repo_relative_path
from codepilot.tools.patch_utils import extract_paths_from_patch


def extract_changed_files_from_patch(patch_path: str | Path) -> list[str]:
    """从 patch 文件提取路径列表，保持首次出现顺序。"""

    path = Path(patch_path)
    if not path.exists():
        return []
    patch_text = path.read_text(encoding="utf-8")
    if not patch_text.strip():
        return []
    return extract_paths_from_patch(patch_text)


def get_diff_stat(repo: str | Path) -> str | None:
    """只读取精简 diff stat，避免把完整 diff 塞进 metadata。"""

    try:
        output = run_git(repo, ["diff", "--stat"])
    except GitCommandError:
        return None
    return output[:4000] or None


def get_untracked_files(repo: str | Path) -> list[str]:
    """返回 git ls-files --others --exclude-standard 的结果。"""

    try:
        output = run_git(repo, ["ls-files", "--others", "--exclude-standard"])
    except GitCommandError:
        return []
    if not output:
        return []
    return [normalize_repo_relative_path(line) for line in output.splitlines() if line.strip()]


def build_untracked_file_patch(repo: str | Path, relative_path: str) -> str:
    """为单个 untracked file 生成 unified diff 片段。"""

    repo_path = Path(repo).expanduser().resolve()
    file_path = (repo_path / relative_path).resolve()
    if not file_path.exists() or not file_path.is_file():
        return ""
    if file_path.stat().st_size > 1024 * 1024:
        return ""
    data = file_path.read_bytes()
    if b"\x00" in data:
        return ""
    text = data.decode("utf-8")
    new_lines = text.splitlines(keepends=True)
    diff_lines = list(
        unified_diff(
            [],
            new_lines,
            fromfile="/dev/null",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    if not diff_lines:
        return ""
    return "\n".join([f"diff --git a/{relative_path} b/{relative_path}", "new file mode 100644", *diff_lines]) + "\n"


def compute_patch_metadata(
    repo: str | Path,
    patch_path: str | Path,
    *,
    base_head_sha: str | None = None,
    effective_head_sha: str | None = None,
    baseline_dirty: bool = False,
    contains_preexisting_changes: bool | None = None,
    protected_paths: list[str] | None = None,
    protected_after_files: list[str] | None = None,
) -> PatchMetadata:
    """围绕已导出的 patch 文件计算摘要信息，供 summary / manifest 复用。"""

    resolved_patch_path = Path(patch_path).expanduser().resolve()
    if not resolved_patch_path.exists():
        raise FileNotFoundError(f"Patch file not found: {resolved_patch_path}")
    patch_text = resolved_patch_path.read_text(encoding="utf-8")
    changed_files = extract_changed_files_from_patch(resolved_patch_path)
    untracked_files = get_untracked_files(repo)
    seen = set(changed_files)
    for path in untracked_files:
        if path in seen:
            continue
        changed_files.append(path)
        seen.add(path)
    untracked_files_omitted = [path for path in untracked_files if path not in extract_changed_files_from_patch(resolved_patch_path)]
    protected_changed_files = find_protected_paths(changed_files, protected_paths or [])
    return PatchMetadata(
        patch_path=resolved_patch_path,
        is_empty=not patch_text.strip() and not untracked_files,
        size_bytes=resolved_patch_path.stat().st_size,
        sha256=sha256_file(resolved_patch_path),
        changed_files=changed_files,
        diff_stat=get_diff_stat(repo),
        base_head_sha=base_head_sha,
        effective_head_sha=effective_head_sha,
        baseline_dirty=baseline_dirty,
        contains_preexisting_changes=contains_preexisting_changes,
        generated_from_repo=Path(repo).expanduser().resolve(),
        protected_changed_files=protected_changed_files,
        untracked_files=untracked_files,
        untracked_files_omitted=untracked_files_omitted,
        protected_after_files=list(protected_after_files or []),
    )
