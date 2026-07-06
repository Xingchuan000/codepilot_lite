from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.auto_pr.github_client import FakeGitHubClient
from codepilot.auto_pr.models import GitHubRepoRef
from codepilot.auto_pr.pr_creator import (
    build_pr_create_request,
    create_pr_if_allowed,
    extract_pr_title,
    sanitize_pr_title,
    validate_pr_body_path,
)


def test_sanitize_pr_title_removes_newlines() -> None:
    assert sanitize_pr_title("a\nb") == "a b"


def test_sanitize_pr_title_truncates_long_title() -> None:
    assert len(sanitize_pr_title("x" * 200)) == 120


def test_extract_pr_title_prefers_issue_json(tmp_path: Path) -> None:
    body = tmp_path / "pr_body.md"
    issue = tmp_path / "issue.json"
    body.write_text("# body title\n", encoding="utf-8")
    issue.write_text(json.dumps({"title": "issue title"}), encoding="utf-8")

    assert extract_pr_title(pr_body_path=body, issue_json_path=issue) == "issue title"


def test_extract_pr_title_reads_pr_body_heading(tmp_path: Path) -> None:
    body = tmp_path / "pr_body.md"
    body.write_text("# body title\n", encoding="utf-8")

    assert extract_pr_title(pr_body_path=body) == "body title"


def test_validate_pr_body_path_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_pr_body_path(tmp_path / "missing.md")


def test_validate_pr_body_path_empty_raises(tmp_path: Path) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        validate_pr_body_path(path)


def test_validate_pr_body_path_token_string_raises(tmp_path: Path) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("github_pat_123456789012345678901234567890\n", encoding="utf-8")

    with pytest.raises(ValueError):
        validate_pr_body_path(path)


def test_build_pr_create_request_default_draft_true(tmp_path: Path) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("# title\n", encoding="utf-8")

    assert build_pr_create_request(
        repo=GitHubRepoRef(owner="o", repo="r"),
        pr_body_path=path,
        title="title",
        head_branch="codepilot/x",
        base_branch="main",
    ).draft is True


def test_build_pr_create_request_rejects_equal_head_and_base(tmp_path: Path) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("# title\n", encoding="utf-8")

    with pytest.raises(ValueError):
        build_pr_create_request(
            repo=GitHubRepoRef(owner="o", repo="r"),
            pr_body_path=path,
            title="title",
            head_branch="main",
            base_branch="main",
        )


def test_create_pr_if_allowed_dry_run_does_not_call_client(tmp_path: Path) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("# title\n", encoding="utf-8")
    client = FakeGitHubClient()
    request = build_pr_create_request(
        repo=GitHubRepoRef(owner="o", repo="r"),
        pr_body_path=path,
        title="title",
        head_branch="codepilot/x",
        base_branch="main",
    )

    assert create_pr_if_allowed(
        client=client,
        request=request,
        execute=False,
        allow_create_pr=True,
        push_executed=True,
        remote_ref_verified=True,
    ).api_called is False


@pytest.mark.parametrize(
    ("allow_create_pr", "push_executed", "remote_ref_verified"),
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_create_pr_if_allowed_requires_all_conditions(
    tmp_path: Path,
    allow_create_pr: bool,
    push_executed: bool,
    remote_ref_verified: bool,
) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("# title\n", encoding="utf-8")
    client = FakeGitHubClient()
    request = build_pr_create_request(
        repo=GitHubRepoRef(owner="o", repo="r"),
        pr_body_path=path,
        title="title",
        head_branch="codepilot/x",
        base_branch="main",
    )

    assert create_pr_if_allowed(
        client=client,
        request=request,
        execute=True,
        allow_create_pr=allow_create_pr,
        push_executed=push_executed,
        remote_ref_verified=remote_ref_verified,
    ).api_called is False


def test_create_pr_if_allowed_calls_fake_client_when_ready(tmp_path: Path) -> None:
    path = tmp_path / "pr_body.md"
    path.write_text("# title\n", encoding="utf-8")
    client = FakeGitHubClient()
    request = build_pr_create_request(
        repo=GitHubRepoRef(owner="o", repo="r"),
        pr_body_path=path,
        title="title",
        head_branch="codepilot/x",
        base_branch="main",
    )

    assert create_pr_if_allowed(
        client=client,
        request=request,
        execute=True,
        allow_create_pr=True,
        push_executed=True,
        remote_ref_verified=True,
    ).url == "https://github.com/o/r/pull/123"
