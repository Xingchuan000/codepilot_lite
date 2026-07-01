from __future__ import annotations

import difflib
import subprocess
import tempfile
from pathlib import Path
from time import perf_counter

from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms
from codepilot.tools.file_tools import _safe_join
from codepilot.tools.patch_utils import extract_paths_from_patch


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """把长文本截断成适合返回给用户的预览。"""

    if len(text) <= max_chars:
        return text, False
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}", True


def _unified_diff_preview(path: str, old_text: str, new_text: str, max_preview_chars: int) -> tuple[str, bool]:
    """生成统一 diff 预览，并按需截断。"""

    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return _truncate_text(diff, max_preview_chars)


def replace_range(
    repo: str | Path,
    path: str,
    start_line: int,
    end_line: int,
    replacement: str,
    dry_run: bool = False,
    max_preview_chars: int = 4000,
) -> ToolResult:
    """替换文件中的一段行区间。"""

    start = perf_counter()
    if start_line < 1:
        return ToolResult(
            success=False,
            error="start_line must be >= 1",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )
    if end_line < start_line:
        return ToolResult(
            success=False,
            error="end_line must be >= start_line",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )

    try:
        target = _safe_join(repo, path)
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )

    if not target.exists():
        return ToolResult(
            success=False,
            error=f"File does not exist: {path}",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )
    if target.is_dir():
        return ToolResult(
            success=False,
            error=f"Path is a directory: {path}",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )
    if not target.is_file():
        return ToolResult(
            success=False,
            error=f"Path is not a regular file: {path}",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )

    old_text = target.read_text(encoding="utf-8", errors="replace")
    lines = old_text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        return ToolResult(
            success=False,
            error="start_line exceeds total lines: 0",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )
    if start_line > total_lines:
        return ToolResult(
            success=False,
            error=f"start_line exceeds total lines: {total_lines}",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )
    if end_line > total_lines:
        return ToolResult(
            success=False,
            error=f"end_line exceeds total lines: {total_lines}",
            metadata={
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "dry_run": dry_run,
                "risk": ToolRisk.LOCAL_WRITE.value,
                "duration_ms": elapsed_ms(start),
            },
        )

    replacement_lines = replacement.splitlines(keepends=True)
    new_text = "".join(lines[: start_line - 1] + replacement_lines + lines[end_line:])
    changed = new_text != old_text
    diff_preview, preview_truncated = _unified_diff_preview(path, old_text, new_text, max_preview_chars)

    if not dry_run and changed:
        target.write_text(new_text, encoding="utf-8")

    return ToolResult(
        success=True,
        output=diff_preview,
        output_summary=f"Replaced lines {start_line}-{end_line} in {path}." if changed else f"No changes produced for {path}.",
        metadata={
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "replacement_lines": len(replacement_lines),
            "dry_run": dry_run,
            "changed": changed,
            "preview_truncated": preview_truncated,
            "duration_ms": elapsed_ms(start),
            "risk": ToolRisk.LOCAL_WRITE.value,
        },
    )


def apply_patch(
    repo: str | Path,
    patch: str,
    dry_run: bool = False,
    max_preview_chars: int = 4000,
) -> ToolResult:
    """用 git apply 将 unified diff 应用到仓库工作区。"""

    start = perf_counter()
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists():
        return ToolResult(
            success=False,
            error=f"Repo does not exist: {repo}",
            metadata={"dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )
    if not repo_path.is_dir():
        return ToolResult(
            success=False,
            error=f"Repo is not a directory: {repo}",
            metadata={"dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )
    if not isinstance(patch, str) or not patch.strip():
        return ToolResult(
            success=False,
            error="patch must not be empty",
            metadata={"dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )

    touched_paths = extract_paths_from_patch(patch)
    if not touched_paths:
        return ToolResult(
            success=False,
            error="Patch does not contain extractable file paths.",
            metadata={"dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            patch_file = Path(tmpdir) / "patch.diff"
            patch_file.write_text(patch, encoding="utf-8")

            check = subprocess.run(
                ["git", "apply", "--check", str(patch_file)],
                cwd=repo_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
            if check.returncode != 0:
                check_output, preview_truncated = _truncate_text(check.stdout or "", max_preview_chars)
                return ToolResult(
                    success=False,
                    output=check_output,
                    output_summary="Patch check failed.",
                    error=f"Patch check failed with returncode {check.returncode}.",
                    metadata={
                        "touched_paths": touched_paths,
                        "dry_run": dry_run,
                        "check_returncode": check.returncode,
                        "apply_returncode": None,
                        "duration_ms": elapsed_ms(start),
                        "risk": ToolRisk.LOCAL_WRITE.value,
                        "preview_truncated": preview_truncated,
                        "suggestion": "Read the latest file content and regenerate a smaller patch.",
                    },
                )

            if dry_run:
                return ToolResult(
                    success=True,
                    output=f"Patch check succeeded for {len(touched_paths)} file(s): {', '.join(touched_paths)}",
                    output_summary=f"Patch check succeeded for {len(touched_paths)} file(s).",
                    metadata={
                        "touched_paths": touched_paths,
                        "dry_run": True,
                        "check_returncode": 0,
                        "apply_returncode": None,
                        "duration_ms": elapsed_ms(start),
                        "risk": ToolRisk.LOCAL_WRITE.value,
                    },
                )

            apply = subprocess.run(
                ["git", "apply", str(patch_file)],
                cwd=repo_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
            if apply.returncode != 0:
                apply_output, preview_truncated = _truncate_text(apply.stdout or "", max_preview_chars)
                return ToolResult(
                    success=False,
                    output=apply_output,
                    output_summary="Patch apply failed.",
                    error=f"Patch apply failed with returncode {apply.returncode}.",
                    metadata={
                        "touched_paths": touched_paths,
                        "dry_run": False,
                        "check_returncode": 0,
                        "apply_returncode": apply.returncode,
                        "duration_ms": elapsed_ms(start),
                        "risk": ToolRisk.LOCAL_WRITE.value,
                        "preview_truncated": preview_truncated,
                        "suggestion": "Inspect the repository state and retry with a smaller patch.",
                    },
                )

            return ToolResult(
                success=True,
                output=f"Applied patch to {len(touched_paths)} file(s): {', '.join(touched_paths)}",
                output_summary=f"Applied patch to {len(touched_paths)} file(s).",
                metadata={
                    "touched_paths": touched_paths,
                    "dry_run": False,
                    "check_returncode": 0,
                    "apply_returncode": 0,
                    "duration_ms": elapsed_ms(start),
                    "risk": ToolRisk.LOCAL_WRITE.value,
                },
            )
    except subprocess.TimeoutExpired:
        return ToolResult(
            success=False,
            error="Patch command timed out.",
            metadata={"touched_paths": touched_paths, "dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )
    except FileNotFoundError:
        return ToolResult(
            success=False,
            error="git executable not found.",
            metadata={"touched_paths": touched_paths, "dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )
    except OSError as exc:
        return ToolResult(
            success=False,
            error=f"Patch command failed: {exc}",
            metadata={"touched_paths": touched_paths, "dry_run": dry_run, "duration_ms": elapsed_ms(start), "risk": ToolRisk.LOCAL_WRITE.value},
        )
