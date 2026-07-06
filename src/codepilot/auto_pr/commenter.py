from __future__ import annotations

"""issue comment 构造与可选回写。"""

import json
import re
from pathlib import Path
from typing import Any

from codepilot.auto_pr.github_client import GitHubClientProtocol
from codepilot.auto_pr.models import GitHubRepoRef
from codepilot.pr_assist.manifest_loader import scan_token_like_strings


def extract_issue_number(source_artifact_manifest: dict[str, Any], issue_json_path: str | Path | None = None) -> int | None:
    """尽量从 issue.json 中恢复 issue number，失败时返回 None。"""

    if issue_json_path is not None and Path(issue_json_path).exists():
        try:
            issue = json.loads(Path(issue_json_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issue = None
        if isinstance(issue, dict):
            ref = issue.get("ref")
            if isinstance(ref, dict) and isinstance(ref.get("number"), int):
                return ref["number"]
            if isinstance(issue.get("number"), int):
                return issue["number"]
            if isinstance(ref, dict) and isinstance(ref.get("url"), str):
                match = re.search(r"/issues/([0-9]+)", ref["url"])
                if match:
                    return int(match.group(1))
    return None


def _redact_absolute_paths_preserving_urls(line: str) -> str:
    """保留 URL，只替换本地绝对路径片段。"""

    tokens = line.split()
    redacted_tokens: list[str] = []
    for token in tokens:
        if token.startswith(("http://", "https://")):
            redacted_tokens.append(token)
            continue
        replaced = re.sub(r"(?:(?<=^)|(?<=[(:]))(/[^\s)]+)", "[REDACTED_PATH]", token)
        redacted_tokens.append(replaced)
    return " ".join(redacted_tokens)


def sanitize_comment_body(value: str) -> str:
    """清理 comment 文本，避免 token、绝对路径或完整 diff 泄露出去。"""

    lines: list[str] = []
    for line in value.splitlines():
        if "diff --git" in line:
            continue
        if scan_token_like_strings(line) or any(token in line.lower() for token in ["ghp_", "github_pat_", "token", "secret"]):
            continue
        lines.append(_redact_absolute_paths_preserving_urls(line))
    return "\n".join(lines)[:4000]


def build_issue_comment_body(
    *,
    pr_url: str | None,
    run_id: str,
    safety_summary: str,
    artifact_summary: list[str],
    dry_run: bool,
) -> str:
    """生成 issue comment 的固定模板。"""

    mode = "dry-run" if dry_run else "execute"
    lines = [
        "CodePilot Lite controlled auto PR result",
        f"- Run ID: {run_id}",
        f"- Mode: {mode}",
        f"- PR: {pr_url or 'not created'}",
        f"- Safety: {safety_summary}",
        f"- Artifacts: {', '.join(artifact_summary)}",
    ]
    return sanitize_comment_body("\n".join(lines))


def post_issue_comment_if_allowed(
    *,
    client: GitHubClientProtocol,
    repo: GitHubRepoRef,
    issue_number: int | None,
    body: str,
    execute: bool,
    allow_comment: bool,
) -> dict[str, Any] | None:
    """仅在 execute + allow_comment + issue_number 可用时回写 issue comment。"""

    if not execute or not allow_comment or issue_number is None:
        return None
    return client.post_issue_comment(repo, issue_number, sanitize_comment_body(body))
