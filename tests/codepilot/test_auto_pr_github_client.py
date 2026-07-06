from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codepilot.auto_pr.github_client import FakeGitHubClient, RestGitHubClient, redact_github_error
from codepilot.auto_pr.models import AutoPRGitHubError, GitHubRepoRef, PRCreateRequest


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def _request(tmp_path: Path) -> PRCreateRequest:
    path = tmp_path / "pr_body.md"
    path.write_text("# title\n", encoding="utf-8")
    return PRCreateRequest(
        repo=GitHubRepoRef(owner="owner", repo="repo"),
        title="title",
        body_path=path,
        head_branch="codepilot/x",
        base_branch="main",
    )


def test_fake_github_client_create_pr_returns_fake_url(tmp_path: Path) -> None:
    result = FakeGitHubClient().create_pull_request(_request(tmp_path))

    assert result.url == "https://github.com/owner/repo/pull/123"


def test_fake_github_client_records_created_requests(tmp_path: Path) -> None:
    client = FakeGitHubClient()
    request = _request(tmp_path)
    client.create_pull_request(request)

    assert client.created_requests == [request]


def test_fake_github_client_fail_create_returns_created_false(tmp_path: Path) -> None:
    assert FakeGitHubClient(fail_create=True).create_pull_request(_request(tmp_path)).created is False


def test_rest_github_client_requires_token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(AutoPRGitHubError):
        RestGitHubClient().create_pull_request(_request(tmp_path))


def test_rest_github_client_create_pull_request_uses_expected_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(_FakeResponse(201, payload={"number": 1, "html_url": "https://github.com/owner/repo/pull/1"}, headers={"X-GitHub-Request-Id": "req"}))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    result = RestGitHubClient(session=session).create_pull_request(_request(tmp_path))

    assert result.created is True
    assert session.calls[0]["url"].endswith("/repos/owner/repo/pulls")
    assert session.calls[0]["json"]["title"] == "title"
    assert session.calls[0]["json"]["head"] == "codepilot/x"
    assert session.calls[0]["json"]["base"] == "main"
    assert session.calls[0]["json"]["draft"] is True


def test_rest_github_client_error_does_not_leak_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(_FakeResponse(403, text="forbidden ghp_secret"))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    result = RestGitHubClient(session=session).create_pull_request(_request(tmp_path))

    assert result.api_called is True
    assert "ghp_secret" not in (result.error or "")


def test_redact_github_error_removes_token_like_text() -> None:
    redacted = redact_github_error("Bearer ghp_secret github_pat_secret")

    assert "ghp_secret" not in redacted
    assert "github_pat_secret" not in redacted


def test_post_issue_comment_uses_issue_comment_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(_FakeResponse(201, payload={"id": 1}))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    RestGitHubClient(session=session).post_issue_comment(GitHubRepoRef(owner="owner", repo="repo"), 7, "body")

    assert session.calls[0]["url"].endswith("/repos/owner/repo/issues/7/comments")
