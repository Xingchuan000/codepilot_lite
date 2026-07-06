from __future__ import annotations

"""PR 标题、正文校验与受控创建逻辑。"""

import json
import re
from pathlib import Path

from codepilot.auto_pr.github_client import GitHubClientProtocol
from codepilot.auto_pr.models import GitHubRepoRef, PRCreateRequest, PRCreateResult
from codepilot.pr_assist.manifest_loader import scan_token_like_strings


def sanitize_pr_title(value: str | None) -> str:
    """把标题压缩为单行、无控制字符、长度受控的字符串。"""

    if value is None or not value.strip():
        return "CodePilot generated patch"
    cleaned = "".join((" " if ch in "\r\n\t" else ch) for ch in value if ch == "\n" or ch == "\r" or ch == "\t" or (ch >= " " and ch != "\x7f"))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or "CodePilot generated patch")[:120]


def extract_pr_title(*, pr_body_path: str | Path, issue_json_path: str | Path | None = None, fallback: str | None = None) -> str:
    """优先 issue.json.title，其次 pr_body 第一行标题，最后 fallback。"""

    if issue_json_path is not None and Path(issue_json_path).exists():
        try:
            data = json.loads(Path(issue_json_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("title"), str):
            return sanitize_pr_title(data["title"])
    first_line = Path(pr_body_path).read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
    if first_line and first_line[0].startswith("# "):
        return sanitize_pr_title(first_line[0].removeprefix("# ").strip())
    return sanitize_pr_title(fallback)


def validate_pr_body_path(pr_body_path: str | Path) -> Path:
    """确保 PR 正文文件存在、非空、且不包含敏感信息或完整 diff。"""

    path = Path(pr_body_path)
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("pr_body.md must not be empty")
    if scan_token_like_strings(text):
        raise ValueError("pr_body.md contains token-like string")
    if text.count("diff --git") > 5:
        raise ValueError("pr_body.md contains too many full diff markers")
    return path


def build_pr_create_request(
    *,
    repo: GitHubRepoRef,
    pr_body_path: str | Path,
    title: str,
    head_branch: str,
    base_branch: str,
    draft: bool = True,
) -> PRCreateRequest:
    """组装 PRCreateRequest，并在入口处做最小安全校验。"""

    body_path = validate_pr_body_path(pr_body_path)
    if not head_branch:
        raise ValueError("head_branch must not be empty")
    if not base_branch:
        raise ValueError("base_branch must not be empty")
    if head_branch == base_branch:
        raise ValueError("head_branch must not equal base_branch")
    return PRCreateRequest(
        repo=repo,
        title=sanitize_pr_title(title),
        body_path=body_path,
        head_branch=head_branch,
        base_branch=base_branch,
        draft=draft,
    )


def create_pr_if_allowed(
    *,
    client: GitHubClientProtocol,
    request: PRCreateRequest,
    execute: bool,
    allow_create_pr: bool,
    push_executed: bool,
    remote_ref_verified: bool,
) -> PRCreateResult:
    """只在 push 已完成且远端引用已核验后才允许创建 PR。"""

    if not execute:
        return PRCreateResult(created=False, api_called=False)
    if not allow_create_pr:
        return PRCreateResult(created=False, api_called=False)
    if not push_executed:
        return PRCreateResult(created=False, api_called=False, error="push not executed")
    if not remote_ref_verified:
        return PRCreateResult(created=False, api_called=False, error="remote ref not verified")
    return client.create_pull_request(request)
