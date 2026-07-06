from __future__ import annotations

import pytest

from codepilot.pr_feedback.github_client import RestPRFeedbackGitHubClient, assert_github_token_available
from codepilot.pr_feedback.models import PRFeedbackGitHubError, PRRef


class _FakeResponse:
    def __init__(self, status_code: int, text: str, *, request_id: str = "req-1") -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"X-GitHub-Request-Id": request_id}

    def json(self) -> dict[str, object]:
        return {}

    def iter_content(self, chunk_size: int = 8192):
        yield b""


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def get(self, url: str, headers: dict[str, str], timeout: int, stream: bool = False) -> _FakeResponse:
        return self.response

    def post(self, url: str, json: dict[str, object], headers: dict[str, str], timeout: int) -> _FakeResponse:
        return self.response


def test_rest_client_missing_token_error_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(PRFeedbackGitHubError):
        assert_github_token_available()


def test_rest_client_403_error_records_request_id_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ABCDEF1234567890")
    client = RestPRFeedbackGitHubClient(session=_FakeSession(_FakeResponse(403, "token ghp_SECRET")), base_url="https://api.github.com")

    with pytest.raises(PRFeedbackGitHubError) as excinfo:
        client.get_pull_request(PRRef(owner="o", repo="r", pull_number=1, url="https://example.com", head_branch="codepilot/test", base_branch="main"))

    assert "status=403" in str(excinfo.value)
    assert "request_id=req-1" in str(excinfo.value)
    assert "ghp_SECRET" not in str(excinfo.value)
