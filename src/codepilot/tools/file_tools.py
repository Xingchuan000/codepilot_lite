"""文件类工具。

第二步只提供读文件和目录浏览，不做编辑、不做补丁，也不接 LLM。
"""

from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from time import perf_counter

from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms

DEFAULT_LIST_FILES_PAGE_SIZE = 200
MAX_LIST_FILES_PAGE_SIZE = 1000
LIST_FILES_PAGE_MAX_CHARS = 12000


def _safe_join(repo: str | Path, path: str | Path) -> Path:
    """把用户路径限制在仓库根目录内。

    这里必须用 resolve + relative_to 做真实路径判断，不能只靠字符串前缀。
    """

    repo_path = Path(repo).resolve()
    target = (repo_path / path).resolve()
    try:
        target.relative_to(repo_path)
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {path}") from exc
    return target


def _is_hidden(path: Path) -> bool:
    """判断路径是否是隐藏项。

    这里按名字判断即可，避免把 `.git`、`.env` 之类目录暴露给默认列表。
    """

    return path.name.startswith(".")


def _relative_display(base: Path, target: Path) -> str:
    """把绝对路径转成相对显示路径，并统一使用 POSIX 分隔符。

    符号链接单独标成 `@`，这样模型能直接看出它不是可递归目录。
    """

    relative = target.relative_to(base)
    display = relative.as_posix()
    if target.is_symlink():
        return f"{display}@"
    return f"{display}/" if target.is_dir() else display


def _is_recursive_directory(path: Path) -> bool:
    """判断是否允许继续递归。

    这里明确禁止递归进入任何符号链接目录，避免越过仓库边界或陷入环。
    """

    return path.is_dir() and not path.is_symlink()


def _iter_directory_entries(
    directory: Path,
    base: Path,
    max_depth: int,
    include_hidden: bool,
) -> Iterator[str]:
    """递归惰性遍历目录，保持稳定顺序。

    使用 relative_to(base).parts 计算每个条目相对于 base 的真实深度，
    确保只有 relative_depth <= max_depth 的条目才被返回。
    """

    for child in sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if not include_hidden and _is_hidden(child):
            continue

        # 基于真实路径计算相对深度，避免递归参数穿透导致的“多一层”问题
        relative_depth = len(child.relative_to(base).parts)
        if relative_depth > max_depth:
            continue

        yield _relative_display(base, child)

        # 只有相对深度尚未达到上限的目录才继续递归
        if _is_recursive_directory(child) and relative_depth < max_depth:
            yield from _iter_directory_entries(child, base, max_depth, include_hidden)


def _take_directory_page(
    entries: Iterator[str],
    *,
    offset: int,
    max_entries: int,
    max_chars: int,
) -> tuple[list[str], bool, str]:
    """从惰性目录流中切出当前页，并判断后面是否还有内容。"""

    page_source = islice(entries, offset, None)
    page: list[str] = []
    output_chars = 0

    for entry in page_source:
        separator_chars = 1 if page else 0
        required_chars = separator_chars + len(entry)

        # 当当前页已经达到条目上限时，直接保留当前页并告诉调用方后面还有内容。
        if len(page) >= max_entries:
            return page, True, "entry_limit"

        # 当前页已经有内容时，不能让拼接后的字符数超过预算。
        if page and output_chars + required_chars > max_chars:
            return page, True, "char_limit"

        # 极端情况下单条路径就超过字符预算，也要完整返回，避免分页永远卡在同一条。
        if not page and len(entry) > max_chars:
            page.append(entry)
            output_chars += len(entry)
            continue

        page.append(entry)
        output_chars += required_chars

    return page, False, "end"


def list_files(
    repo: str | Path,
    path: str = ".",
    max_depth: int = 2,
    include_hidden: bool = False,
    max_entries: int = DEFAULT_LIST_FILES_PAGE_SIZE,
    offset: int = 0,
) -> ToolResult:
    """列出目录树。

    只做目录浏览，不做任何额外兜底逻辑。
    """

    start = perf_counter()
    try:
        if offset < 0:
            return ToolResult(
                success=False,
                error="offset must be >= 0",
                metadata={
                    "path": path,
                    "max_depth": max_depth,
                    "include_hidden": include_hidden,
                    "offset": offset,
                    "max_entries": max_entries,
                    "risk": ToolRisk.READ_ONLY.value,
                    "duration_ms": elapsed_ms(start),
                },
            )
        if max_entries < 1:
            return ToolResult(
                success=False,
                error="max_entries must be >= 1",
                metadata={
                    "path": path,
                    "max_depth": max_depth,
                    "include_hidden": include_hidden,
                    "offset": offset,
                    "max_entries": max_entries,
                    "risk": ToolRisk.READ_ONLY.value,
                    "duration_ms": elapsed_ms(start),
                },
            )
        if max_entries > MAX_LIST_FILES_PAGE_SIZE:
            return ToolResult(
                success=False,
                error=f"max_entries must be <= {MAX_LIST_FILES_PAGE_SIZE}",
                metadata={
                    "path": path,
                    "max_depth": max_depth,
                    "include_hidden": include_hidden,
                    "offset": offset,
                    "max_entries": max_entries,
                    "risk": ToolRisk.READ_ONLY.value,
                    "duration_ms": elapsed_ms(start),
                },
            )

        base = _safe_join(repo, path)
        if not base.exists():
            return ToolResult(
                success=False,
                error=f"Path does not exist: {path}",
                metadata={
                    "path": path,
                    "max_depth": max_depth,
                    "include_hidden": include_hidden,
                    "offset": offset,
                    "max_entries": max_entries,
                    "risk": ToolRisk.READ_ONLY.value,
                    "duration_ms": elapsed_ms(start),
                },
            )
        if not base.is_dir():
            return ToolResult(
                success=False,
                error=f"Path is not a directory: {path}",
                metadata={
                    "path": path,
                    "max_depth": max_depth,
                    "include_hidden": include_hidden,
                    "offset": offset,
                    "max_entries": max_entries,
                    "risk": ToolRisk.READ_ONLY.value,
                    "duration_ms": elapsed_ms(start),
                },
            )

        entries, has_more, limit_reason = _take_directory_page(
            _iter_directory_entries(base, base, max_depth, include_hidden),
            offset=offset,
            max_entries=max_entries,
            max_chars=LIST_FILES_PAGE_MAX_CHARS,
        )
        output = "\n".join(entries)
        next_offset = offset + len(entries) if has_more else None
        if has_more:
            if limit_reason == "entry_limit":
                summary = f"Listed {len(entries)} entries under {path} from offset {offset}; more entries available at offset {next_offset}."
            else:
                summary = f"Listed {len(entries)} entries under {path} from offset {offset}; page character limit reached, continue at offset {next_offset}."
        elif offset == 0:
            summary = f"Listed {len(entries)} entries under {path}"
        else:
            summary = f"Listed {len(entries)} entries under {path} from offset {offset}; end of listing."

        return ToolResult(
            success=True,
            output=output,
            output_summary=summary,
            metadata={
                "path": path,
                "max_depth": max_depth,
                "include_hidden": include_hidden,
                "follow_symlinks": False,
                "offset": offset,
                "max_entries": max_entries,
                "entries_returned": len(entries),
                "has_more": has_more,
                "next_offset": next_offset,
                "truncated": has_more,
                "limit_reason": limit_reason,
                "page_output_chars": len(output),
                "page_max_chars": LIST_FILES_PAGE_MAX_CHARS,
                "duration_ms": elapsed_ms(start),
                "risk": ToolRisk.READ_ONLY.value,
            },
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={
                "path": path,
                "max_depth": max_depth,
                "include_hidden": include_hidden,
                "follow_symlinks": False,
                "offset": offset,
                "max_entries": max_entries,
                "risk": ToolRisk.READ_ONLY.value,
                "duration_ms": elapsed_ms(start),
            },
        )


def _format_line(line_number: int, line: str) -> str:
    """把行号和内容拼成稳定的阅读格式。"""

    return f"{line_number:>4}: {line}"


def read_file(
    repo: str | Path,
    path: str,
    start_line: int = 1,
    end_line: int = 120,
    max_chars: int = 12000,
) -> ToolResult:
    """读取文件片段并保留行号。"""

    start = perf_counter()
    try:
        if start_line < 1:
            return ToolResult(
                success=False,
                error="start_line must be >= 1",
                metadata={"path": path, "start_line": start_line, "end_line": end_line, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )
        if end_line < start_line:
            return ToolResult(
                success=False,
                error="end_line must be >= start_line",
                metadata={"path": path, "start_line": start_line, "end_line": end_line, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )

        target = _safe_join(repo, path)
        if not target.exists():
            return ToolResult(
                success=False,
                error=f"File does not exist: {path}",
                metadata={"path": path, "start_line": start_line, "end_line": end_line, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )
        if target.is_dir():
            return ToolResult(
                success=False,
                error=f"Path is a directory: {path}",
                metadata={"path": path, "start_line": start_line, "end_line": end_line, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            actual_start_line = 0
            actual_end_line = 0
        else:
            actual_start_line = min(max(start_line, 1), total_lines + 1)
            actual_end_line = min(end_line, total_lines)
        selected = []
        if actual_start_line <= actual_end_line:
            for line_number in range(actual_start_line, actual_end_line + 1):
                selected.append(_format_line(line_number, lines[line_number - 1]))

        output = "\n".join(selected)
        truncated = len(output) > max_chars
        if truncated:
            output = f"{output[: max(0, max_chars - len('... truncated'))]}... truncated"

        return ToolResult(
            success=True,
            output=output,
            output_summary=f"Read {path} lines {actual_start_line}-{actual_end_line} of {total_lines}.",
            metadata={
                "path": path,
                "start_line": start_line,
                "actual_start_line": actual_start_line,
                "end_line": actual_end_line,
                "total_lines": total_lines,
                "truncated": truncated,
                "duration_ms": elapsed_ms(start),
                "risk": ToolRisk.READ_ONLY.value,
            },
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"path": path, "start_line": start_line, "end_line": end_line, "risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )
