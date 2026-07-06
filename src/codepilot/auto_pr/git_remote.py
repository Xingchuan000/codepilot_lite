from __future__ import annotations

"""GitHub remote 与 push plan 解析工具。"""

from pathlib import Path
from urllib.parse import urlparse

from codepilot.auto_pr.models import AutoPRGitError, AutoPRRemoteError, BranchPushPlan, GitHubRepoRef
from codepilot.auto_pr.workflow_inputs import sanitize_branch_component, validate_head_branch, validate_repo_slug
from codepilot.repo import git_utils


def parse_github_remote_url(remote_url: str) -> GitHubRepoRef:
    """解析常见 GitHub remote URL，只接受 github.com。"""

    cleaned = remote_url.strip()
    if not cleaned:
        raise AutoPRRemoteError("remote URL must not be empty")
    if cleaned.startswith("git@github.com:"):
        path = cleaned.removeprefix("git@github.com:").removesuffix(".git")
    elif cleaned.startswith("ssh://git@github.com/"):
        path = cleaned.removeprefix("ssh://git@github.com/").removesuffix(".git")
    else:
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != "github.com":
            raise AutoPRRemoteError("only github.com remotes are supported")
        path = parsed.path.lstrip("/").removesuffix(".git")
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        raise AutoPRRemoteError("remote URL must contain owner/repo")
    return GitHubRepoRef(owner=parts[0], repo=parts[1], remote_url=remote_url)


def get_current_branch(repo_path: str | Path) -> str:
    """读取当前本地分支；游离 HEAD 时明确报错。"""

    branch = git_utils.get_current_branch(repo_path)
    if branch is None:
        raise AutoPRGitError("current branch is not available")
    return branch


def get_default_base_branch(repo_path: str | Path, remote_name: str = "origin") -> str | None:
    """优先从 remote HEAD 解析默认基线分支。"""

    return git_utils.get_remote_head_branch(repo_path, remote_name)


def sanitize_remote_branch_name(run_id: str, prefix: str = "codepilot") -> str:
    """生成固定命名空间下的安全远端分支名。"""

    return f"{sanitize_branch_component(prefix)}/{sanitize_branch_component(run_id)}"


def assert_branch_name_safe(
    *,
    remote_branch: str,
    base_branch: str,
    protected_names: set[str] | None = None,
) -> None:
    """确保 push 目标分支不会落到受保护引用上。"""

    protected = protected_names or {"main", "master"}
    if not remote_branch.startswith("codepilot/"):
        raise AutoPRRemoteError("remote_branch must start with codepilot/")
    if remote_branch == base_branch:
        raise AutoPRRemoteError("remote_branch must not equal base_branch")
    if remote_branch in protected:
        raise AutoPRRemoteError("remote_branch points to a protected branch")
    if remote_branch in {f"refs/heads/{name}" for name in protected | {base_branch}}:
        raise AutoPRRemoteError("remote_branch must not use refs/heads protected names")


def build_push_plan(
    *,
    repo_path: str | Path,
    remote_name: str,
    local_branch: str,
    remote_branch: str,
    base_branch: str,
    commit_sha: str,
) -> BranchPushPlan:
    """构造受控 push 计划，但这里不执行任何 remote 操作。"""

    assert_branch_name_safe(remote_branch=remote_branch, base_branch=base_branch)
    return BranchPushPlan(
        remote_name=remote_name,
        local_branch=local_branch,
        remote_branch=remote_branch,
        commit_sha=commit_sha,
        base_branch=base_branch,
        push_refspec=f"HEAD:refs/heads/{remote_branch}",
    )


def resolve_repo_ref(repo_path: str | Path, *, remote_name: str = "origin", repo_slug: str | None = None) -> GitHubRepoRef:
    """优先使用显式 repo_slug，否则从 remote URL 推导 owner/repo。"""

    if repo_slug:
        owner, repo = validate_repo_slug(repo_slug).split("/", maxsplit=1)
        remote_url = None
        try:
            remote_url = git_utils.get_remote_url(repo_path, remote_name)
        except Exception:
            remote_url = None
        return GitHubRepoRef(owner=owner, repo=repo, remote_url=remote_url)
    remote_url = git_utils.get_remote_url(repo_path, remote_name)
    return parse_github_remote_url(remote_url)
