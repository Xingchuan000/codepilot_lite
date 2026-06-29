"""Shell 执行工具。

这是第二步里唯一的高风险工具，所以明确标成 ask 权限，不做任何额外兜底。
"""

from dataclasses import dataclass
import os
import signal
import subprocess
from pathlib import Path
from time import perf_counter

from codepilot.tools.base import ToolResult, ToolRisk, elapsed_ms


@dataclass
class _CommandRun:
    """内部执行结果，方便把超时和 returncode 分开记录。"""

    output: str
    returncode: int
    timed_out: bool


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """尽量结束整个进程树，避免 shell 残留。"""

    if os.name == "nt":
        process.kill()
        return
    os.killpg(process.pid, signal.SIGKILL)


def _run_command(repo_path: Path, command: str, timeout: int) -> _CommandRun:
    """在仓库目录下执行命令，并把 stdout/stderr 合并到一起。"""

    env = os.environ.copy()
    env.update(
        {
            "PAGER": "cat",
            "MANPAGER": "cat",
            "PIP_PROGRESS_BAR": "off",
            "TQDM_DISABLE": "1",
        }
    )
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=repo_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        return _CommandRun(output=output or "", returncode=process.returncode or 0, timed_out=False)
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        output, _ = process.communicate()
        return _CommandRun(output=output or "", returncode=process.returncode or -1, timed_out=True)


def run_shell(
    repo: str | Path,
    command: str,
    timeout: int = 30,
    max_output_chars: int = 12000,
) -> ToolResult:
    """在仓库根目录下执行 shell 命令。"""

    start = perf_counter()
    repo_path = Path(repo).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        return ToolResult(
            success=False,
            error=f"Repository directory does not exist: {repo}",
            metadata={"command": command, "cwd": str(repo_path), "returncode": -1, "timeout": timeout, "risk": ToolRisk.SHELL_EXECUTION.value, "duration_ms": elapsed_ms(start)},
        )

    try:
        run = _run_command(repo_path, command, timeout)
        output = run.output
        truncated = len(output) > max_output_chars
        if truncated:
            output = f"{output[: max(0, max_output_chars - len('... truncated'))]}... truncated"

        if run.timed_out:
            return ToolResult(
                success=False,
                output=output,
                error=f"Command timed out after {timeout} seconds.",
                output_summary=f"Command timed out after {timeout} seconds.",
                metadata={
                    "command": command,
                    "cwd": str(repo_path),
                    "returncode": run.returncode,
                    "timeout": timeout,
                    "truncated": truncated,
                    "duration_ms": elapsed_ms(start),
                    "risk": ToolRisk.SHELL_EXECUTION.value,
                },
            )

        success = run.returncode == 0
        return ToolResult(
            success=success,
            output=output,
            error=None if success else f"Command failed with returncode {run.returncode}.",
            output_summary=f"Command {'succeeded' if success else 'failed'} with returncode {run.returncode}.",
            metadata={
                "command": command,
                "cwd": str(repo_path),
                "returncode": run.returncode,
                "timeout": timeout,
                "truncated": truncated,
                "duration_ms": elapsed_ms(start),
                "risk": ToolRisk.SHELL_EXECUTION.value,
            },
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"command": command, "cwd": str(repo_path), "returncode": -1, "timeout": timeout, "risk": ToolRisk.SHELL_EXECUTION.value, "duration_ms": elapsed_ms(start)},
        )
