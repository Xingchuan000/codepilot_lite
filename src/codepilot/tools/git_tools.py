from __future__ import annotations

"""Git 只读工具。

第七步只新增 git_status 和 git_diff，两者都保持只读。
这里不做自动修复、自动提交或任何计划之外的扩展行为。
"""

from pathlib import Path
import re
import subprocess
from time import perf_counter

from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms


def _resolve_repo(repo: str | Path) -> tuple[Path | None, ToolResult | None]:
    """把 repo 解析成绝对目录路径。"""

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        return None, ToolResult(success=False, error=f"Repository directory does not exist: {repo}")
    if not repo_path.is_dir():
        return None, ToolResult(success=False, error=f"Repository path is not a directory: {repo}")
    return repo_path, None


def _run_git(repo_path: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """执行 git 子命令并合并 stdout/stderr。"""

    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        shell=False,
    )


def _truncate_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    """按字符数截断文本。"""

    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _truncate_lines(text: str, *, max_lines: int) -> tuple[str, bool]:
    """按行数截断文本。"""

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]), True


def _is_git_repo(repo_path: Path) -> bool:
    """判断目录是否位于 git work tree 内。"""

    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
        shell=False,
    )
    return completed.returncode == 0 and (completed.stdout or "").strip() == "true"


def _redact_secret_like_lines(diff_text: str) -> tuple[str, bool]:
    """对 diff 中明显像密钥的整行做轻量脱敏。"""

    patterns = [
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*.+"),
        re.compile(r"-----BEGIN .*PRIVATE KEY-----"),
        re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    ]
    redacted = False
    lines: list[str] = []
    for line in diff_text.splitlines():
        replaced = line
        for pattern in patterns:
            if pattern.search(replaced):
                prefix = replaced[:1] if replaced[:1] in {"+", "-", " "} else ""
                replaced = f"{prefix}[REDACTED]"
                redacted = True
                break
        lines.append(replaced)
    return "\n".join(lines), redacted


def _looks_large_or_generated(path: str) -> bool:
    """标记计划中定义的常见大文件或生成产物。"""

    return (
        path in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock"}
        or path.startswith("dist/")
        or path.startswith("build/")
        or path.endswith(".min.js")
        or path.endswith(".map")
        or path.endswith(".zip")
        or path.endswith(".tar")
        or path.endswith(".gz")
    )


def git_status(
    repo: str | Path,
    max_entries: int = 200,
) -> ToolResult:
    """返回 git status --short 的结构化摘要。"""

    start = perf_counter()
    repo_path, error = _resolve_repo(repo)
    if error is not None:
        return error.model_copy(update={"metadata": {"risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)}})
    if max_entries <= 0:
        return ToolResult(
            success=False,
            error="max_entries must be greater than 0.",
            metadata={"risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )

    try:
        if not _is_git_repo(repo_path):
            return ToolResult(
                success=False,
                error=f"Repository is not a git repository: {repo_path}",
                metadata={"risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
            )
        completed = _run_git(repo_path, ["status", "--short"])
    except FileNotFoundError:
        return ToolResult(
            success=False,
            error="git executable not found.",
            metadata={"risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            success=False,
            error="git status command timed out.",
            metadata={"risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )

    if completed.returncode != 0:
        return ToolResult(
            success=False,
            error=(completed.stdout or "").strip() or f"git status failed with returncode {completed.returncode}.",
            metadata={"risk": ToolRisk.READ_ONLY.value, "duration_ms": elapsed_ms(start)},
        )

    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    truncated = len(lines) > max_entries
    visible_lines = lines[:max_entries]
    changed_files: list[str] = []
    staged_files: list[str] = []
    unstaged_files: list[str] = []
    untracked_files: list[str] = []
    deleted_files: list[str] = []
    renamed_files: list[dict[str, str]] = []

    # git status --short 的前两列分别表示 staged / unstaged 状态，这里按计划逐行解析。
    for line in visible_lines:
        status = line[:2]
        raw_path = line[3:]
        path = raw_path
        if "R" in status and " -> " in raw_path:
            old_path, new_path = raw_path.split(" -> ", 1)
            renamed_files.append({"old": old_path, "new": new_path})
            path = new_path
        changed_files.append(path)
        if status == "??":
            untracked_files.append(path)
        if status[0] not in {" ", "?"}:
            staged_files.append(path)
        if status[1] not in {" ", "?"}:
            unstaged_files.append(path)
        if "D" in status:
            deleted_files.append(path)

    metadata = {
        "changed_files": changed_files,
        "changed_count": len(changed_files),
        "clean": len(changed_files) == 0,
        "truncated": truncated,
        "staged_files": staged_files,
        "unstaged_files": unstaged_files,
        "untracked_files": untracked_files,
        "deleted_files": deleted_files,
        "renamed_files": renamed_files,
        "duration_ms": elapsed_ms(start),
        "risk": ToolRisk.READ_ONLY.value,
    }
    if not changed_files:
        return ToolResult(success=True, output="", output_summary="Repository is clean.", metadata=metadata)
    return ToolResult(
        success=True,
        output="\n".join(visible_lines),
        output_summary=f"Repository has {len(changed_files)} changed file(s).",
        metadata=metadata,
    )


def git_diff(
    repo: str | Path,
    path: str | None = None,
    staged: bool = False,
    include_content: bool = False,
    max_lines: int = 300,
    max_chars: int = 12000,
) -> ToolResult:
    """返回 git diff 摘要或指定路径的 diff 内容。"""

    start = perf_counter()
    base_metadata = {
        "path": path,
        "staged": staged,
        "include_content": include_content,
        "risk": ToolRisk.READ_ONLY.value,
    }
    repo_path, error = _resolve_repo(repo)
    if error is not None:
        return error.model_copy(update={"metadata": {**base_metadata, "duration_ms": elapsed_ms(start)}})
    if max_lines <= 0:
        return ToolResult(success=False, error="max_lines must be greater than 0.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})
    if max_chars <= 0:
        return ToolResult(success=False, error="max_chars must be greater than 0.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})
    if include_content and path is None:
        return ToolResult(
            success=False,
            error="git_diff include_content=True requires a specific path.",
            metadata={**base_metadata, "duration_ms": elapsed_ms(start)},
        )

    try:
        if not _is_git_repo(repo_path):
            return ToolResult(
                success=False,
                error=f"Repository is not a git repository: {repo_path}",
                metadata={**base_metadata, "duration_ms": elapsed_ms(start)},
            )
    except FileNotFoundError:
        return ToolResult(success=False, error="git executable not found.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error="git rev-parse command timed out.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})

    try:
        if not include_content:
            name_status_cmd = ["diff", "--name-status"]
            stat_cmd = ["diff", "--stat"]
            if staged:
                name_status_cmd.append("--cached")
                stat_cmd.append("--cached")
            if path:
                name_status_cmd.extend(["--", path])
                stat_cmd.extend(["--", path])
            name_status = _run_git(repo_path, name_status_cmd)
            stat = _run_git(repo_path, stat_cmd)
            if name_status.returncode != 0:
                return ToolResult(success=False, error=(name_status.stdout or "").strip() or f"git diff failed with returncode {name_status.returncode}.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})
            if stat.returncode != 0:
                return ToolResult(success=False, error=(stat.stdout or "").strip() or f"git diff --stat failed with returncode {stat.returncode}.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})

            name_status_output = (name_status.stdout or "").strip()
            stat_output = (stat.stdout or "").strip()
            output = "No diff."
            if name_status_output or stat_output:
                output = f"Changed files:\n{name_status_output}\n\nDiff stat:\n{stat_output}".strip()
            changed_paths = []
            for line in name_status_output.splitlines():
                parts = line.split("\t")
                if not parts:
                    continue
                if parts[0].startswith("R") and len(parts) >= 3:
                    changed_paths.append(parts[-1])
                elif len(parts) >= 2:
                    changed_paths.append(parts[-1])
            metadata = {
                **base_metadata,
                "line_truncated": False,
                "char_truncated": False,
                "truncated": False,
                "lines_returned": len(output.splitlines()),
                "chars_returned": len(output),
                "has_secret_like_content": False,
                "binary_diff": False,
                "large_or_generated_files": [item for item in changed_paths if _looks_large_or_generated(item)],
                "duration_ms": elapsed_ms(start),
            }
            return ToolResult(success=True, output=output, output_summary="No diff." if output == "No diff." else "Returned git diff summary.", metadata=metadata)

        cmd = ["diff", "--no-ext-diff"]
        if staged:
            cmd.append("--cached")
        cmd.extend(["--", path])
        completed = _run_git(repo_path, cmd)
    except FileNotFoundError:
        return ToolResult(success=False, error="git executable not found.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error="git diff command timed out.", metadata={**base_metadata, "duration_ms": elapsed_ms(start)})

    if completed.returncode != 0:
        return ToolResult(
            success=False,
            error=(completed.stdout or "").strip() or f"git diff failed with returncode {completed.returncode}.",
            metadata={**base_metadata, "duration_ms": elapsed_ms(start)},
        )

    diff_text = completed.stdout or ""
    redacted_output, has_secret_like_content = _redact_secret_like_lines(diff_text)
    line_limited_output, line_truncated = _truncate_lines(redacted_output, max_lines=max_lines)
    output, char_truncated = _truncate_text(line_limited_output, max_chars=max_chars)
    binary_diff = "Binary files " in diff_text and " differ" in diff_text
    metadata = {
        "path": path,
        "staged": staged,
        "include_content": include_content,
        "line_truncated": line_truncated,
        "char_truncated": char_truncated,
        "truncated": line_truncated or char_truncated,
        "lines_returned": len(output.splitlines()),
        "chars_returned": len(output),
        "has_secret_like_content": has_secret_like_content,
        "binary_diff": binary_diff,
        "large_or_generated_files": [path] if path and _looks_large_or_generated(path) else [],
        "duration_ms": elapsed_ms(start),
        "risk": ToolRisk.READ_ONLY.value,
    }
    summary = "Returned git diff content."
    if has_secret_like_content:
        summary = "Returned git diff content (secret-like content redacted)."
    return ToolResult(success=True, output=output, output_summary=summary, metadata=metadata)
