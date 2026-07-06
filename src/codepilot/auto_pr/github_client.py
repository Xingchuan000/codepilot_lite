from __future__ import annotations

"""GitHub API client，严格避免把 token 写入日志或产物。"""

import os
import re
from typing import Any, Protocol

import requests

from codepilot.auto_pr.models import AutoPRGitHubError, GitHubRepoRef, PRCreateRequest, PRCreateResult


class GitHubClientProtocol(Protocol):
    """抽象 GitHub client，便于 workflow 注入 fake client 做测试。"""

    def create_pull_request(self, request: PRCreateRequest) -> PRCreateResult: ...

    def post_issue_comment(self, repo: GitHubRepoRef, issue_number: int, body: str) -> dict[str, Any]: ...


def is_github_token_available(token_env: str = "GITHUB_TOKEN") -> bool:
    """只检查 GitHub 凭据是否存在，不返回任何敏感值。"""

    return bool(os.environ.get(token_env))


def assert_github_token_available(token_env: str = "GITHUB_TOKEN") -> None:
    """在真正创建 PR 前做最小凭据预检查。"""

    if not is_github_token_available(token_env):
        raise AutoPRGitHubError("missing required GitHub credential for PR creation")


def redact_github_error(value: str) -> str:
    """清理 GitHub 错误消息中的 token，再截断成适合展示的长度。"""

    redacted = re.sub(r"ghp_[A-Za-z0-9_]+", "[REDACTED]", value)
    redacted = re.sub(r"github_pat_[A-Za-z0-9_]+", "[REDACTED]", redacted)
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"(?i)GITHUB_TOKEN", "GitHub credential", redacted)
    redacted = re.sub(r"(?i)OPENAI_API_KEY", "API credential", redacted)
    redacted = re.sub(r"(?i)ANTHROPIC_API_KEY", "API credential", redacted)
    redacted = re.sub(
        r"missing GitHub token env:\s*[A-Za-z0-9_]+",
        "missing required GitHub credential for PR creation",
        redacted,
    )
    return redacted[:500]


class FakeGitHubClient:
    """纯内存 fake client，用于单元测试 workflow 与 CLI 路径。"""

    def __init__(self, *, fail_create: bool = False, fail_comment: bool = False) -> None:
        self.fail_create = fail_create
        self.fail_comment = fail_comment
        self.created_requests: list[PRCreateRequest] = []
        self.comments: list[dict[str, Any]] = []

    def create_pull_request(self, request: PRCreateRequest) -> PRCreateResult:
        self.created_requests.append(request)
        if self.fail_create:
            return PRCreateResult(created=False, api_called=True, error="fake create failure")
        return PRCreateResult(
            created=True,
            number=123,
            url=f"https://github.com/{request.repo.owner}/{request.repo.repo}/pull/123",
            api_called=True,
            request_id="fake-request",
            status_code=201,
        )

    def post_issue_comment(self, repo: GitHubRepoRef, issue_number: int, body: str) -> dict[str, Any]:
        if self.fail_comment:
            raise AutoPRGitHubError("fake comment failure")
        payload = {"repo": repo, "issue_number": issue_number, "body": body}
        self.comments.append(payload)
        return {"posted": True, "issue_number": issue_number}


class RestGitHubClient:
    """最小 REST client，只实现创建 PR 与 issue comment。"""

    def __init__(
        self,
        *,
        token_env: str = "GITHUB_TOKEN",
        session: requests.Session | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self.token_env = token_env
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def _token(self) -> str:
        assert_github_token_available(self.token_env)
        return str(os.environ.get(self.token_env))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/vnd.github+json",
        }

    def create_pull_request(self, request: PRCreateRequest) -> PRCreateResult:
        url = f"{self.base_url}/repos/{request.repo.owner}/{request.repo.repo}/pulls"
        body = request.body_path.read_text(encoding="utf-8")
        try:
            response = self.session.post(
                url,
                json={
                    "title": request.title,
                    "body": body,
                    "head": request.head_branch,
                    "base": request.base_branch,
                    "draft": request.draft,
                },
                headers=self._headers(),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise AutoPRGitHubError(redact_github_error(str(exc))) from exc
        request_id = response.headers.get("X-GitHub-Request-Id")
        if response.status_code in {200, 201}:
            payload = response.json()
            return PRCreateResult(
                created=True,
                number=payload.get("number"),
                url=payload.get("html_url"),
                api_called=True,
                request_id=request_id,
                status_code=response.status_code,
            )
        return PRCreateResult(
            created=False,
            api_called=True,
            request_id=request_id,
            status_code=response.status_code,
            error=redact_github_error(response.text),
        )

    def post_issue_comment(self, repo: GitHubRepoRef, issue_number: int, body: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{repo.owner}/{repo.repo}/issues/{issue_number}/comments"
        try:
            response = self.session.post(
                url,
                json={"body": body},
                headers=self._headers(),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise AutoPRGitHubError(redact_github_error(str(exc))) from exc
        if response.status_code not in {200, 201}:
            raise AutoPRGitHubError(redact_github_error(response.text))
        return response.json()
