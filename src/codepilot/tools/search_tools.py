"""源码搜索工具。

第二步使用纯 Python 实现，避免依赖用户机器是否安装 `rg`。
"""

from fnmatch import fnmatch
from pathlib import Path
from time import perf_counter

from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms
from codepilot.tools.file_tools import _safe_join

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


def _iter_search_files(base: Path, file_glob: str) -> list[Path]:
    """收集需要搜索的文件，并跳过默认排除目录。"""

    if base.is_file():
        return [base] if fnmatch(base.name, file_glob) else []

    files: list[Path] = []
    for child in sorted(base.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if child.is_dir():
            if child.name in DEFAULT_EXCLUDE_DIRS:
                continue
            files.extend(_iter_search_files(child, file_glob))
            continue
        if fnmatch(child.name, file_glob):
            files.append(child)
    return files


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
        base = _safe_join(repo, path)
        if not base.exists():
            return ToolResult(
                success=False,
                error=f"Path does not exist: {path}",
                metadata={"query": query, "path": path, "file_glob": file_glob, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )

        query_key = query if case_sensitive else query.lower()
        results: list[str] = []
        for file_path in _iter_search_files(base, file_glob):
            relative = file_path.relative_to(Path(repo).resolve()).as_posix()
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except IsADirectoryError:
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
            },
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"query": query, "path": path, "file_glob": file_glob, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )
