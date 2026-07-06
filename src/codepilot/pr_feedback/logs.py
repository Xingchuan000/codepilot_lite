from __future__ import annotations

"""收集失败 CI job 日志，并压缩成安全摘要。"""

import re
from pathlib import Path

from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol
from codepilot.pr_feedback.models import CILogSummary, CheckRunSummary, PRRef


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_TOKEN_RE = re.compile(
    r"ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|Bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]+|(token|secret|password|api_key)=\S+",
    re.IGNORECASE,
)


def strip_ansi(text: str) -> str:
    """移除终端颜色控制符。"""

    return _ANSI_RE.sub("", text)


def redact_ci_log(text: str) -> str:
    """把日志中的 token-like 内容替换成占位符。"""

    return _TOKEN_RE.sub("[REDACTED]", strip_ansi(text)).replace("\x00", "")


def summarize_ci_log(text: str, *, max_lines: int = 120) -> str:
    """从超长 CI 日志里提取最有用的失败线索。"""

    lines = redact_ci_log(text).splitlines()
    selected: list[str] = []
    traceback_start = None
    for index, line in enumerate(lines):
        if "Traceback (most recent call last):" in line:
            traceback_start = index
        if any(marker in line for marker in ["FAILED ", "ERROR ", "pytest", "ruff", "flake8", "mypy", "pyright", "tsc", "npm ERR!", "build failed"]):
            selected.append(line)
    if traceback_start is not None:
        selected.extend(lines[traceback_start : traceback_start + 40])
    if not selected:
        selected = lines[:max_lines]
    deduped: list[str] = []
    seen: set[str] = set()
    for line in selected:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    truncated = len(deduped) > max_lines
    deduped = deduped[:max_lines]
    if truncated:
        deduped.append("[truncated]")
    return "\n".join(deduped)


def write_ci_log_artifacts(
    *,
    output_dir: str | Path,
    workflow_run_id: int | None,
    job_id: int | None,
    name: str,
    log_text: str,
    max_bytes: int,
) -> CILogSummary:
    """把单个日志写成 `log.txt` 与 `summary.md` 两份 artifact。"""

    output_dir_path = Path(output_dir).expanduser().resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    path = output_dir_path / f"{workflow_run_id or 'unknown'}-{job_id or 'unknown'}.log.txt"
    summary_path = output_dir_path / f"{workflow_run_id or 'unknown'}-{job_id or 'unknown'}.summary.md"
    raw = log_text[:max_bytes]
    redacted = redact_ci_log(raw)
    truncated = len(log_text) > max_bytes
    path.write_text(redacted, encoding="utf-8")
    summary_path.write_text(f"# CI Log Summary\n\n```text\n{summarize_ci_log(redacted)}\n```\n", encoding="utf-8")
    return CILogSummary(
        workflow_run_id=workflow_run_id,
        job_id=job_id,
        name=name,
        path=path,
        summary=summarize_ci_log(redacted),
        truncated=truncated,
        redacted=True,
        bytes_read=min(len(log_text.encode("utf-8")), max_bytes),
        evidence_path=summary_path,
    )


def collect_failed_ci_logs(
    *,
    client: PRFeedbackGitHubClientProtocol,
    pr: PRRef,
    checks: list[CheckRunSummary],
    output_dir: str | Path,
    max_log_bytes: int = 200_000,
    include_success_logs: bool = False,
) -> list[CILogSummary]:
    """只下载失败类 job 的日志，成功日志默认不抓取。"""

    summaries: list[CILogSummary] = []
    for check in checks:
        if check.conclusion not in {"failure", "timed_out", "action_required", "cancelled"}:
            if not include_success_logs:
                continue
            if check.conclusion != "success":
                continue
        if check.workflow_run_id is None or check.job_id is None:
            continue
        log_text = client.download_job_log(pr.owner, pr.repo, check.job_id, max_log_bytes)
        summaries.append(
            write_ci_log_artifacts(
                output_dir=output_dir,
                workflow_run_id=check.workflow_run_id,
                job_id=check.job_id,
                name=check.name,
                log_text=log_text,
                max_bytes=max_log_bytes,
            )
        )
    return summaries
