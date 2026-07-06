from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from codepilot.repo.models import GitFileStatus, GitRepoInfo


class GitCommandError(RuntimeError):
    """包装 git 命令失败信息，避免把敏感环境或长输出直接抛给上层。"""

    def __init__(self, args: list[str], returncode: int, stderr_summary: str) -> None:
        self.args_list = args
        self.returncode = returncode
        self.stderr_summary = stderr_summary[:500]
        super().__init__(f"git {' '.join(args)} failed (returncode={returncode}): {self.stderr_summary}")


def _resolve_repo(repo: str | Path) -> Path:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"Repository path must be an existing directory: {repo_path}")
    return repo_path


def _clean_git_env() -> dict[str, str]:
    """移除 askpass / ssh 注入，避免安全检查意外触发交互或泄露。"""

    env = dict(os.environ)
    for key in ("GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH_COMMAND"):
        env.pop(key, None)
    return env


def run_git(
    repo: str | Path,
    args: list[str],
    *,
    timeout: int = 30,
    check: bool = True,
) -> str:
    """统一执行 git 子命令，并固定 shell=False 与 timeout。"""

    if not isinstance(args, list):
        raise TypeError("args must be list[str]")
    repo_path = _resolve_repo(repo)
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout,
        env=_clean_git_env(),
    )
    if check and result.returncode != 0:
        summary = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()[:500]
        raise GitCommandError(args, result.returncode, summary)
    return result.stdout.strip()


def is_git_repo(repo: str | Path) -> bool:
    try:
        return run_git(repo, ["rev-parse", "--is-inside-work-tree"], check=True) == "true"
    except (ValueError, GitCommandError):
        return False


def get_git_root(repo: str | Path) -> Path:
    return Path(run_git(repo, ["rev-parse", "--show-toplevel"])).resolve()


def get_head_sha(repo: str | Path) -> str | None:
    try:
        return run_git(repo, ["rev-parse", "HEAD"])
    except GitCommandError:
        return None


def get_current_branch(repo: str | Path) -> str | None:
    try:
        branch = run_git(repo, ["branch", "--show-current"])
    except GitCommandError:
        return None
    return branch or None


def get_repo_info(repo: str | Path) -> GitRepoInfo:
    repo_path = Path(repo).expanduser().resolve()
    return GitRepoInfo(
        repo_path=repo_path,
        git_root=get_git_root(repo_path),
        current_branch=get_current_branch(repo_path),
        head_sha=get_head_sha(repo_path),
        is_git_repo=is_git_repo(repo_path),
    )


def get_porcelain_status(repo: str | Path) -> list[GitFileStatus]:
    output = subprocess.run(
        ["git", "-C", str(_resolve_repo(repo)), "status", "--porcelain=v1"],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=30,
        env=_clean_git_env(),
    )
    if output.returncode != 0:
        summary = ((output.stderr or "") + "\n" + (output.stdout or "")).strip()[:500]
        raise GitCommandError(["status", "--porcelain=v1"], output.returncode, summary)
    stdout = output.stdout.rstrip("\n")
    if not stdout:
        return []
    files: list[GitFileStatus] = []
    for line in stdout.splitlines():
        status = line[:2]
        raw_path = line[3:] if len(line) > 3 else ""
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", maxsplit=1)[1]
        files.append(
            GitFileStatus(
                path=raw_path,
                status=status,
                staged=status[0] not in {" ", "?"},
                unstaged=status[1] not in {" ", "?"},
                untracked=status == "??",
            )
        )
    return files


def get_worktree_clean(repo: str | Path) -> bool:
    """返回当前工作区是否完全干净。"""

    return len(get_porcelain_status(repo)) == 0


def get_remote_url(repo: str | Path, remote_name: str = "origin") -> str:
    """读取指定 remote 的 URL。"""

    return run_git(repo, ["remote", "get-url", remote_name])


def ls_remote(repo: str | Path, remote_name: str, ref: str) -> str:
    """读取远端指定引用，失败时把原始 stdout 交给上层判定。"""

    return run_git(repo, ["ls-remote", "--heads", remote_name, ref], timeout=30, check=False)


def get_remote_head_branch(repo: str | Path, remote_name: str = "origin") -> str | None:
    """尽量解析 remote HEAD 指向的默认分支；失败时返回 None。"""

    try:
        symbolic = run_git(repo, ["symbolic-ref", f"refs/remotes/{remote_name}/HEAD"])
    except GitCommandError:
        symbolic = ""
    if symbolic.startswith(f"refs/remotes/{remote_name}/"):
        return symbolic.removeprefix(f"refs/remotes/{remote_name}/")
    try:
        output = run_git(repo, ["remote", "show", remote_name], check=False)
    except GitCommandError:
        return None
    for line in output.splitlines():
        if "HEAD branch:" not in line:
            continue
        branch = line.split("HEAD branch:", maxsplit=1)[1].strip()
        return branch or None
    return None


def get_remote_branch_sha(repo: str | Path, remote_name: str, remote_branch: str) -> str | None:
    """读取 remote 上某个分支当前指向的 sha，不存在时返回 None。"""

    output = ls_remote(repo, remote_name, remote_branch)
    if not output:
        return None
    first_line = output.splitlines()[0].strip()
    if not first_line:
        return None
    parts = first_line.split()
    return parts[0] if parts else None


def sha256_file(path: str | Path) -> str | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
