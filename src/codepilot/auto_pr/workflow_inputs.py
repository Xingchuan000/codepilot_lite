from __future__ import annotations

"""auto-pr workflow 输入校验与分支名清洗。"""

import re
from pathlib import Path

from codepilot.auto_pr.models import AutoPRWorkflowInputError


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
REPO_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_ISSUE_URL_PATTERN = re.compile(r"^https://github\.com/[^/]+/[^/]+/issues/[0-9]+/?$")
_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$<>\\]")
_BRANCH_SAFE_CHAR_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def validate_run_id(value: str) -> str:
    """校验 run_id，确保后续生成分支名与路径时不会越界。"""

    if not value:
        raise AutoPRWorkflowInputError("run_id must not be empty")
    if "/" in value or "\\" in value:
        raise AutoPRWorkflowInputError("run_id must not contain path separators")
    if ".." in value:
        raise AutoPRWorkflowInputError("run_id must not contain '..'")
    if Path(value).is_absolute():
        raise AutoPRWorkflowInputError("run_id must not be an absolute path")
    if not RUN_ID_PATTERN.fullmatch(value):
        raise AutoPRWorkflowInputError("run_id contains unsupported characters")
    return value


def validate_issue_url(value: str | None) -> str | None:
    """只允许标准 GitHub issue URL。"""

    if value is None or not value.strip():
        return None
    if not GITHUB_ISSUE_URL_PATTERN.fullmatch(value.strip()):
        raise AutoPRWorkflowInputError("issue_url must be a GitHub issue URL")
    return value.strip()


def validate_repo_slug(value: str | None) -> str | None:
    """只允许 owner/repo 形式，避免把 URL 或更深路径传进来。"""

    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    if "://" in cleaned or cleaned.startswith("git@"):
        raise AutoPRWorkflowInputError("repo_slug must be owner/repo, not a URL")
    if not REPO_SLUG_PATTERN.fullmatch(cleaned):
        raise AutoPRWorkflowInputError("repo_slug must be owner/repo")
    return cleaned


def sanitize_branch_component(value: str) -> str:
    """把任意输入压缩成安全的单段分支组件。"""

    cleaned = _BRANCH_SAFE_CHAR_PATTERN.sub("-", value)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("/.-")
    return cleaned or "codepilot-run"


def validate_head_branch(value: str | None, *, run_id: str, prefix: str = "codepilot") -> str:
    """确保 head branch 始终落在 codepilot/ 命名空间下。"""

    safe_prefix = sanitize_branch_component(prefix)
    safe_run_id = sanitize_branch_component(validate_run_id(run_id))
    if value is None:
        return f"{safe_prefix}/{safe_run_id}"
    cleaned = value.strip()
    if not cleaned:
        return f"{safe_prefix}/{safe_run_id}"
    if ".." in cleaned or " " in cleaned or _SHELL_METACHAR_PATTERN.search(cleaned):
        raise AutoPRWorkflowInputError("head_branch contains unsafe characters")
    if cleaned in {"main", "master", "HEAD", "refs/heads/main", "refs/heads/master"}:
        raise AutoPRWorkflowInputError("head_branch points to a protected branch")
    if not cleaned.startswith(f"{safe_prefix}/"):
        cleaned = f"{safe_prefix}/{sanitize_branch_component(cleaned)}"
    parts = [sanitize_branch_component(part) for part in cleaned.split("/") if part]
    branch = "/".join(parts[:2]) if len(parts) >= 2 else f"{safe_prefix}/{safe_run_id}"
    if branch in {"main", "master", "HEAD", "refs/heads/main", "refs/heads/master"}:
        raise AutoPRWorkflowInputError("head_branch points to a protected branch")
    if not branch.startswith(f"{safe_prefix}/"):
        raise AutoPRWorkflowInputError("head_branch must stay under codepilot/ namespace")
    return branch
