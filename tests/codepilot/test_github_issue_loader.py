from __future__ import annotations

import json
from pathlib import Path
from urllib import error

import pytest

from codepilot.github.issue_loader import load_issue_from_file, load_issue_from_github, parse_github_issue_url


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def test_load_issue_from_file_uses_first_h1_as_title(tmp_path: Path) -> None:
    path = tmp_path / "issue.md"
    path.write_text("# Demo bug\n\nBody line 1\n\n## Detail\nBody line 2\n", encoding="utf-8")

    issue = load_issue_from_file(path)

    assert issue.title == "Demo bug"
    assert issue.body == "Body line 1\n\n## Detail\nBody line 2"


def test_load_issue_from_file_without_h1_uses_stem(tmp_path: Path) -> None:
    path = tmp_path / "demo_issue.md"
    path.write_text("Body only\n", encoding="utf-8")

    issue = load_issue_from_file(path)

    assert issue.title == "demo_issue"
    assert issue.body == "Body only\n"


def test_load_issue_from_file_empty_markdown_raises(tmp_path: Path) -> None:
    path = tmp_path / "issue.md"
    path.write_text(" \n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_issue_from_file(path)


def test_load_issue_from_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_issue_from_file(tmp_path / "missing.md")


@pytest.mark.parametrize(
    ("url",),
    [
        ("https://github.com/o/r/issues/123",),
        ("github.com/o/r/issues/123",),
    ],
)
def test_parse_github_issue_url_accepts_supported_formats(url: str) -> None:
    ref = parse_github_issue_url(url)

    assert (ref.owner, ref.repo, ref.number) == ("o", "r", 123)


@pytest.mark.parametrize(
    ("url",),
    [
        ("https://github.com/o/r/pull/123",),
        ("https://example.com/o/r/issues/123",),
    ],
)
def test_parse_github_issue_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        parse_github_issue_url(url)


def test_load_issue_from_github_parses_title_body_and_labels(monkeypatch) -> None:
    def fake_urlopen(req, timeout):
        assert req.full_url == "https://api.github.com/repos/openai/codex/issues/123"
        assert req.headers["User-agent"] == "CodePilot-Lite"
        assert req.headers["Authorization"] == "Bearer secret-token"
        payload = json.dumps(
            {
                "title": "Fix bug",
                "body": "Issue body",
                "labels": [{"name": "bug"}, {"name": "good first issue"}],
                "state": "open",
                "html_url": "https://github.com/openai/codex/issues/123",
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-02T00:00:00Z",
            }
        ).encode("utf-8")
        return FakeResponse(payload)

    monkeypatch.setattr("codepilot.github.issue_loader.request.urlopen", fake_urlopen)

    issue = load_issue_from_github("https://github.com/openai/codex/issues/123", token="secret-token")

    assert issue.title == "Fix bug"
    assert issue.body == "Issue body"
    assert issue.labels == ["bug", "good first issue"]
    assert "token" not in issue.metadata
    assert "authorization" not in issue.metadata


def test_load_issue_from_github_rejects_pull_request_payload(monkeypatch) -> None:
    def fake_urlopen(req, timeout):
        return FakeResponse(json.dumps({"pull_request": {}, "title": "PR"}).encode("utf-8"))

    monkeypatch.setattr("codepilot.github.issue_loader.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="pull request"):
        load_issue_from_github("https://github.com/openai/codex/issues/123")


def test_load_issue_from_github_rejects_non_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "codepilot.github.issue_loader.request.urlopen",
        lambda req, timeout: FakeResponse(b"not-json"),
    )

    with pytest.raises(ValueError, match="decode GitHub issue response as JSON"):
        load_issue_from_github("https://github.com/openai/codex/issues/123")


def test_load_issue_from_github_converts_url_error_to_value_error(monkeypatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.URLError("network down")

    monkeypatch.setattr("codepilot.github.issue_loader.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="network down"):
        load_issue_from_github("https://github.com/openai/codex/issues/123")
