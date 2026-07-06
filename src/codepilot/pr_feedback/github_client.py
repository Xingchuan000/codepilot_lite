from __future__ import annotations

"""只读 PR feedback GitHub client 与可测试 fake client。"""

import os
from typing import Any, Protocol

import requests

from codepilot.auto_pr.github_client import redact_github_error
from codepilot.pr_feedback.models import PRFeedbackGitHubError, PRRef


class PRFeedbackGitHubClientProtocol(Protocol):
    """PR feedback workflow 需要的最小 GitHub 访问接口。"""

    def get_pull_request(self, pr: PRRef) -> dict[str, Any]: ...

    def list_check_runs_for_ref(self, pr: PRRef) -> list[dict[str, Any]]: ...

    def list_commit_statuses(self, pr: PRRef) -> list[dict[str, Any]]: ...

    def list_workflow_runs_for_pr(self, pr: PRRef) -> list[dict[str, Any]]: ...

    def list_workflow_jobs(self, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]: ...

    def download_job_log(self, owner: str, repo: str, job_id: int, max_bytes: int) -> str: ...

    def list_pull_request_reviews(self, pr: PRRef) -> list[dict[str, Any]]: ...

    def list_pull_request_review_comments(self, pr: PRRef) -> list[dict[str, Any]]: ...

    def post_pr_comment(self, pr: PRRef, body: str) -> dict[str, Any]: ...


def is_github_token_available(token_env: str = "GITHUB_TOKEN") -> bool:
    """仅检查环境变量是否存在，不回传敏感值。"""

    return bool(os.environ.get(token_env))


def assert_github_token_available(token_env: str = "GITHUB_TOKEN") -> None:
    """在真正访问 GitHub 前做最小凭据检查。"""

    if not is_github_token_available(token_env):
        raise PRFeedbackGitHubError("missing required GitHub credential")


def redact_feedback_text(value: str, *, limit: int = 500) -> str:
    """把反馈文本里的 token-like 串替换掉，再裁剪成可安全展示的长度。"""

    redacted = redact_github_error(value)
    return redacted[:limit]


class FakePRFeedbackGitHubClient:
    """纯内存 fake client，用于单元测试 workflow。"""

    def __init__(
        self,
        *,
        pull_request: dict[str, Any] | None = None,
        check_runs: list[dict[str, Any]] | None = None,
        commit_statuses: list[dict[str, Any]] | None = None,
        workflow_runs: list[dict[str, Any]] | None = None,
        workflow_jobs: dict[int, list[dict[str, Any]]] | None = None,
        job_logs: dict[int, str] | None = None,
        reviews: list[dict[str, Any]] | None = None,
        review_comments: list[dict[str, Any]] | None = None,
        fail: dict[str, str] | None = None,
    ) -> None:
        self.pull_request = pull_request or {}
        self.check_runs = check_runs or []
        self.commit_statuses = commit_statuses or []
        self.workflow_runs = workflow_runs or []
        self.workflow_jobs = workflow_jobs or {}
        self.job_logs = job_logs or {}
        self.reviews = reviews or []
        self.review_comments = review_comments or []
        self.fail = fail or {}
        self.calls: list[dict[str, Any]] = []

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail:
            raise PRFeedbackGitHubError(self.fail[method])

    def _record(self, method: str, **metadata: Any) -> None:
        self.calls.append({"method": method, **metadata})

    def get_pull_request(self, pr: PRRef) -> dict[str, Any]:
        self._maybe_fail("get_pull_request")
        self._record("get_pull_request", pull_number=pr.pull_number)
        return self.pull_request

    def list_check_runs_for_ref(self, pr: PRRef) -> list[dict[str, Any]]:
        self._maybe_fail("list_check_runs_for_ref")
        self._record("list_check_runs_for_ref", head_sha=pr.head_sha)
        return list(self.check_runs)

    def list_commit_statuses(self, pr: PRRef) -> list[dict[str, Any]]:
        self._maybe_fail("list_commit_statuses")
        self._record("list_commit_statuses", head_sha=pr.head_sha)
        return list(self.commit_statuses)

    def list_workflow_runs_for_pr(self, pr: PRRef) -> list[dict[str, Any]]:
        self._maybe_fail("list_workflow_runs_for_pr")
        self._record("list_workflow_runs_for_pr", pull_number=pr.pull_number)
        return list(self.workflow_runs)

    def list_workflow_jobs(self, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        self._maybe_fail("list_workflow_jobs")
        self._record("list_workflow_jobs", owner=owner, repo=repo, run_id=run_id)
        return list(self.workflow_jobs.get(run_id, []))

    def download_job_log(self, owner: str, repo: str, job_id: int, max_bytes: int) -> str:
        self._maybe_fail("download_job_log")
        self._record("download_job_log", owner=owner, repo=repo, job_id=job_id, max_bytes=max_bytes)
        return self.job_logs.get(job_id, "")[:max_bytes]

    def list_pull_request_reviews(self, pr: PRRef) -> list[dict[str, Any]]:
        self._maybe_fail("list_pull_request_reviews")
        self._record("list_pull_request_reviews", pull_number=pr.pull_number)
        return list(self.reviews)

    def list_pull_request_review_comments(self, pr: PRRef) -> list[dict[str, Any]]:
        self._maybe_fail("list_pull_request_review_comments")
        self._record("list_pull_request_review_comments", pull_number=pr.pull_number)
        return list(self.review_comments)

    def post_pr_comment(self, pr: PRRef, body: str) -> dict[str, Any]:
        self._maybe_fail("post_pr_comment")
        self._record("post_pr_comment", pull_number=pr.pull_number, body=body)
        return {"posted": True, "body": body}


class RestPRFeedbackGitHubClient:
    """最小 REST client，只实现第十四步需要的只读接口。"""

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
        self.calls: list[dict[str, Any]] = []

    def _token(self) -> str:
        assert_github_token_available(self.token_env)
        return str(os.environ.get(self.token_env))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/vnd.github+json",
        }

    def _record(self, method: str, endpoint: str, response: requests.Response | None = None) -> None:
        self.calls.append(
            {
                "method": method,
                "endpoint": endpoint,
                "status_code": None if response is None else response.status_code,
                "request_id": None if response is None else response.headers.get("X-GitHub-Request-Id"),
            }
        )

    def _request_json(self, url: str) -> Any:
        try:
            response = self.session.get(url, headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise PRFeedbackGitHubError(redact_github_error(str(exc))) from exc
        self._record("GET", url, response)
        if response.status_code not in {200, 201}:
            raise PRFeedbackGitHubError(redact_github_error(response.text))
        return response.json()

    def _get_list(self, url: str) -> list[dict[str, Any]]:
        payload = self._request_json(url)
        if isinstance(payload, dict):
            for key in ("items", "workflow_runs", "check_runs", "statuses", "jobs"):
                if key in payload:
                    items = payload[key]
                    break
            else:
                items = payload
        else:
            items = payload
        if not isinstance(items, list):
            raise PRFeedbackGitHubError("GitHub response must be a JSON list")
        result: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                result.append(item)
        return result

    def get_pull_request(self, pr: PRRef) -> dict[str, Any]:
        payload = self._request_json(f"{self.base_url}/repos/{pr.owner}/{pr.repo}/pulls/{pr.pull_number}")
        if not isinstance(payload, dict):
            raise PRFeedbackGitHubError("GitHub response must be a JSON object")
        return payload

    def list_check_runs_for_ref(self, pr: PRRef) -> list[dict[str, Any]]:
        return self._get_list(f"{self.base_url}/repos/{pr.owner}/{pr.repo}/commits/{pr.head_sha}/check-runs")

    def list_commit_statuses(self, pr: PRRef) -> list[dict[str, Any]]:
        return self._get_list(f"{self.base_url}/repos/{pr.owner}/{pr.repo}/commits/{pr.head_sha}/statuses")

    def list_workflow_runs_for_pr(self, pr: PRRef) -> list[dict[str, Any]]:
        return self._get_list(f"{self.base_url}/repos/{pr.owner}/{pr.repo}/actions/runs?event=pull_request&branch={pr.head_branch}")

    def list_workflow_jobs(self, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        return self._get_list(f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")

    def download_job_log(self, owner: str, repo: str, job_id: int, max_bytes: int) -> str:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        try:
            response = self.session.get(url, headers=self._headers(), timeout=30, stream=True)
        except requests.RequestException as exc:
            raise PRFeedbackGitHubError(redact_github_error(str(exc))) from exc
        self._record("GET", url, response)
        if response.status_code not in {200, 302}:
            raise PRFeedbackGitHubError(redact_github_error(response.text))
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            part = chunk[:remaining]
            chunks.append(part)
            remaining -= len(part)
            if remaining <= 0:
                break
        data = b"".join(chunks)
        return data[:max_bytes].decode("utf-8", errors="replace")

    def list_pull_request_reviews(self, pr: PRRef) -> list[dict[str, Any]]:
        return self._get_list(f"{self.base_url}/repos/{pr.owner}/{pr.repo}/pulls/{pr.pull_number}/reviews")

    def list_pull_request_review_comments(self, pr: PRRef) -> list[dict[str, Any]]:
        return self._get_list(f"{self.base_url}/repos/{pr.owner}/{pr.repo}/pulls/{pr.pull_number}/comments")

    def post_pr_comment(self, pr: PRRef, body: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{pr.owner}/{pr.repo}/issues/{pr.pull_number}/comments"
        try:
            response = self.session.post(url, json={"body": body}, headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise PRFeedbackGitHubError(redact_github_error(str(exc))) from exc
        self._record("POST", url, response)
        if response.status_code not in {200, 201}:
            raise PRFeedbackGitHubError(redact_github_error(response.text))
        payload = response.json()
        if not isinstance(payload, dict):
            raise PRFeedbackGitHubError("GitHub response must be a JSON object")
        return payload
