"""源码搜索工具。

第二步使用纯 Python 实现，避免依赖用户机器是否安装 `rg`。
"""

from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path
from time import perf_counter

from codepilot.repo.protected_paths import DEFAULT_REPO_PROTECTED_PATHS
from codepilot.repo.safety import normalize_repo_relative_path, path_matches_any
from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}


def _iter_search_files(
    base: Path,
    *,
    repo_root: Path,
    file_glob: str,
    protected_patterns: tuple[str, ...] = tuple(DEFAULT_REPO_PROTECTED_PATHS),
    skipped: dict[str, int] | None = None,
) -> Iterator[Path]:
    """遍历仓库内普通文件，不跟随链接或进入受保护路径。"""

    if _has_symlink_component(base, repo_root):
        _count_skip(skipped, "symlinks")
        return
    if base.is_file():
        relative = _canonical_relative(base, repo_root)
        if path_matches_any(relative, list(protected_patterns)) is None and fnmatch(base.name, file_glob):
            yield base
        elif path_matches_any(relative, list(protected_patterns)) is not None:
            _count_skip(skipped, "protected_paths")
        return

    for child in sorted(base.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if child.is_symlink():
            _count_skip(skipped, "symlinks")
            continue
        relative = _canonical_relative(child, repo_root)
        if path_matches_any(relative, list(protected_patterns)) is not None:
            _count_skip(skipped, "protected_paths")
            continue
        if child.is_dir():
            if child.name in DEFAULT_EXCLUDE_DIRS:
                continue
            yield from _iter_search_files(
                child,
                repo_root=repo_root,
                file_glob=file_glob,
                protected_patterns=protected_patterns,
                skipped=skipped,
            )
            continue
        if child.is_file() and fnmatch(child.name, file_glob):
            yield child


def _count_skip(skipped: dict[str, int] | None, kind: str) -> None:
    if skipped is not None:
        skipped[kind] = skipped.get(kind, 0) + 1


def _has_symlink_component(path: Path, repo_root: Path) -> bool:
    current = repo_root
    for part in path.relative_to(repo_root).parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _safe_search_path(repo_root: Path, path: str | Path) -> Path:
    requested = repo_root / path
    try:
        requested.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {path}") from exc
    return requested


def _canonical_relative(path: Path, repo_root: Path) -> str:
    return normalize_repo_relative_path(path.resolve().relative_to(repo_root))


def search_code(
    repo: str | Path,
    query: str,
    path: str = ".",
    file_glob: str = "*",
    max_results: int = 50,
    case_sensitive: bool = False,
) -> ToolResult:
    """在源码里搜索关键词。"""

    start = perf_counter()
    if not query:
        return ToolResult(
            success=False,
            error="query must not be empty",
            metadata={"query": query, "path": path, "file_glob": file_glob, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )

    try:
        repo_root = Path(repo).resolve()
        base = _safe_search_path(repo_root, path)
        if not base.exists():
            return ToolResult(
                success=False,
                error=f"Path does not exist: {path}",
                metadata={"query": query, "path": path, "file_glob": file_glob, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )

        query_key = query if case_sensitive else query.lower()
        results: list[str] = []
        skipped: dict[str, int] = {}
        for file_path in _iter_search_files(base, repo_root=repo_root, file_glob=file_glob, skipped=skipped):
            relative = _canonical_relative(file_path, repo_root)
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (IsADirectoryError, PermissionError, OSError):
                _count_skip(skipped, "unreadable")
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if query_key not in haystack:
                    continue
                results.append(f"{relative}:{line_number}: {line}")
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        truncated = len(results) >= max_results
        output = "\n".join(results) if results else "No matches found."
        # 截断时在 output 末尾追加一行提示，方便人工查看
        if truncated:
            output += f"\n... truncated after {max_results} results"
        summary = f"Found {len(results)} matches for '{query}'." if results else f"No matches found for '{query}'."
        if truncated and results:
            summary = f"{summary} Output truncated."

        return ToolResult(
            success=True,
            output=output,
            output_summary=summary,
            metadata={
                "query": query,
                "path": path,
                "file_glob": file_glob,
                "results_returned": len(results),
                "truncated": truncated,
                "duration_ms": elapsed_ms(start),
                "risk": ToolRisk.READ_ONLY.value,
                "protected_paths_skipped": skipped.get("protected_paths", 0),
                "symlinks_skipped": skipped.get("symlinks", 0),
                "unreadable_skipped": skipped.get("unreadable", 0),
            },
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"query": query, "path": path, "file_glob": file_glob, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )
