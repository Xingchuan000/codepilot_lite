from __future__ import annotations

"""测试执行工具。

这个工具只做计划里要求的事情：
- 用 shell=False 执行用户显式给出的测试命令
- 截断原始输出
- 调用 pytest 摘要器生成更短的可读结果
"""

import os
from pathlib import Path
import shlex
import subprocess
from time import perf_counter

from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms
from codepilot.tools.test_summary import summarize_test_output


def _invalid_result(
    start: float,
    repo_path: Path,
    command: str,
    timeout: int,
    max_output_chars: int,
    max_summary_chars: int,
    error: str,
) -> ToolResult:
    """统一生成输入校验失败结果。"""

    return ToolResult(
        success=False,
        error=error,
        metadata={
            "command": command,
            "argv": [],
            "cwd": str(repo_path),
            "returncode": -1,
            "timeout": timeout,
            "timed_out": False,
            "status": "failed",
            "summary_line": None,
            "failed_tests": [],
            "failed_tests_truncated": False,
            "relevant_output_truncated": False,
            "raw_output_chars": 0,
            "output_chars": 0,
            "output_truncated": False,
            "max_output_chars": max_output_chars,
            "max_summary_chars": max_summary_chars,
            "duration_ms": elapsed_ms(start),
            "risk": ToolRisk.LOCAL_EXECUTION.value,
        },
    )


def run_tests(
    repo: str | Path,
    command: str = "pytest",
    timeout: int = 60,
    max_output_chars: int = 12000,
    max_summary_chars: int = 6000,
) -> ToolResult:
    """在仓库目录中执行测试命令并返回摘要结果。"""

    start = perf_counter()
    repo_path = Path(repo).resolve()

    # 这一层按计划只做显式输入校验，校验失败直接返回结构化错误，不抛异常。
    if not repo_path.exists():
        return _invalid_result(start, repo_path, command, timeout, max_output_chars, max_summary_chars, f"Repository directory does not exist: {repo}")
    if not repo_path.is_dir():
        return _invalid_result(start, repo_path, command, timeout, max_output_chars, max_summary_chars, f"Repository path is not a directory: {repo}")
    if not isinstance(command, str) or not command.strip():
        return _invalid_result(start, repo_path, command, timeout, max_output_chars, max_summary_chars, "Test command must be a non-empty string.")
    if timeout <= 0:
        return _invalid_result(start, repo_path, command, timeout, max_output_chars, max_summary_chars, "timeout must be greater than 0.")
    if max_output_chars <= 0:
        return _invalid_result(start, repo_path, command, timeout, max_output_chars, max_summary_chars, "max_output_chars must be greater than 0.")
    if max_summary_chars <= 0:
        return _invalid_result(start, repo_path, command, timeout, max_output_chars, max_summary_chars, "max_summary_chars must be greater than 0.")

    argv = shlex.split(command)
    env = os.environ.copy()
    env.update(
        {
            "PAGER": "cat",
            "MANPAGER": "cat",
            "PIP_PROGRESS_BAR": "off",
            "TQDM_DISABLE": "1",
            "PYTHONUNBUFFERED": "1",
            # 第八步的默认测试路径不应因为执行 pytest 而在仓库里生成 __pycache__，
            # 否则会污染 git_status / changed_files 的最小演示输出。
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    try:
        completed = subprocess.run(
            argv,
            cwd=repo_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            shell=False,
            env=env,
        )
        raw_output = completed.stdout or ""
        timed_out = False
        returncode = completed.returncode
    except FileNotFoundError as exc:
        return ToolResult(
            success=False,
            error=f"Test executable not found: {exc.filename or command}",
            metadata={
                "command": command,
                "argv": argv,
                "cwd": str(repo_path),
                "returncode": -1,
                "timeout": timeout,
                "timed_out": False,
                "status": "failed",
                "summary_line": None,
                "failed_tests": [],
                "failed_tests_truncated": False,
                "relevant_output_truncated": False,
                "raw_output_chars": 0,
                "output_chars": 0,
                "output_truncated": False,
                "duration_ms": elapsed_ms(start),
                "risk": ToolRisk.LOCAL_EXECUTION.value,
            },
        )
    except subprocess.TimeoutExpired as exc:
        raw_output = exc.stdout or ""
        timed_out = True
        returncode = -1
    except OSError as exc:
        return ToolResult(
            success=False,
            error=f"Test command failed to start: {exc}",
            metadata={
                "command": command,
                "argv": argv,
                "cwd": str(repo_path),
                "returncode": -1,
                "timeout": timeout,
                "timed_out": False,
                "status": "failed",
                "summary_line": None,
                "failed_tests": [],
                "failed_tests_truncated": False,
                "relevant_output_truncated": False,
                "raw_output_chars": 0,
                "output_chars": 0,
                "output_truncated": False,
                "duration_ms": elapsed_ms(start),
                "risk": ToolRisk.LOCAL_EXECUTION.value,
            },
        )

    raw_output_chars = len(raw_output)
    output_for_summary = raw_output[:max_output_chars]
    output_truncated = raw_output_chars > max_output_chars
    summary = summarize_test_output(
        output_for_summary,
        returncode=returncode,
        timed_out=timed_out,
        max_chars=max_summary_chars,
    )

    if timed_out:
        formatted_output = (
            f"Test Status: Timed out\n"
            f"Command: {command}\n"
            f"Return code: -1\n"
            f"Summary: Test command timed out after {timeout} seconds."
        )
        if summary.relevant_output:
            formatted_output += f"\n\nRelevant output:\n{summary.relevant_output}"
        metadata = {
            "command": command,
            "argv": argv,
            "cwd": str(repo_path),
            "returncode": -1,
            "timeout": timeout,
            "timed_out": True,
            "status": summary.status,
            "summary_line": summary.summary_line,
            "failed_tests": summary.failed_tests,
            "failed_tests_truncated": summary.failed_tests_truncated,
            "relevant_output_truncated": summary.relevant_output_truncated,
            "raw_output_chars": raw_output_chars,
            "output_chars": len(formatted_output),
            "output_truncated": output_truncated,
            "duration_ms": elapsed_ms(start),
            "risk": ToolRisk.LOCAL_EXECUTION.value,
            "suggestion": "Run a narrower test command or increase timeout if appropriate.",
        }
        return ToolResult(
            success=False,
            output=formatted_output,
            output_summary=f"Test command timed out after {timeout} seconds.",
            error=f"Test command timed out after {timeout} seconds.",
            metadata=metadata,
        )

    formatted_output = (
        f"Test Status: {'Passed' if summary.status == 'passed' else 'Failed'}\n"
        f"Command: {command}\n"
        f"Return code: {returncode}\n"
        f"Summary: {summary.summary_line or f'returncode {returncode}'}"
    )
    if summary.failed_tests:
        formatted_output += "\n\nFailed tests:\n" + "\n".join(f"- {name}" for name in summary.failed_tests)
    if summary.relevant_output:
        formatted_output += f"\n\nRelevant output:\n{summary.relevant_output}"

    metadata = {
        "command": command,
        "argv": argv,
        "cwd": str(repo_path),
        "returncode": returncode,
        "timeout": timeout,
        "timed_out": False,
        "status": summary.status,
        "summary_line": summary.summary_line,
        "failed_tests": summary.failed_tests,
        "failed_tests_truncated": summary.failed_tests_truncated,
        "relevant_output_truncated": summary.relevant_output_truncated,
        "raw_output_chars": raw_output_chars,
        "output_chars": len(formatted_output),
        "output_truncated": output_truncated,
        "duration_ms": elapsed_ms(start),
        "risk": ToolRisk.LOCAL_EXECUTION.value,
    }
    if summary.status == "failed":
        metadata["suggestion"] = "Inspect the failing tests and relevant source files, then make a smaller targeted edit."
        return ToolResult(
            success=False,
            output=formatted_output,
            output_summary=f"Tests failed: {summary.summary_line or f'returncode {returncode}'}.",
            error=f"Test command failed with returncode {returncode}.",
            metadata=metadata,
        )

    return ToolResult(
        success=True,
        output=formatted_output,
        output_summary=f"Tests passed: {summary.summary_line or 'returncode 0'}.",
        error=None,
        metadata=metadata,
    )
