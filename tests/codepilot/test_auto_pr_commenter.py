from __future__ import annotations

import json
from pathlib import Path

from codepilot.auto_pr.commenter import (
    build_issue_comment_body,
    extract_issue_number,
    post_issue_comment_if_allowed,
    sanitize_comment_body,
)
from codepilot.auto_pr.github_client import FakeGitHubClient
from codepilot.auto_pr.models import GitHubRepoRef


def test_post_issue_comment_default_none_when_not_execute() -> None:
    assert post_issue_comment_if_allowed(
        client=FakeGitHubClient(),
        repo=GitHubRepoRef(owner="o", repo="r"),
        issue_number=1,
        body="body",
        execute=False,
        allow_comment=True,
    ) is None


def test_post_issue_comment_allow_comment_false_returns_none() -> None:
    assert post_issue_comment_if_allowed(
        client=FakeGitHubClient(),
        repo=GitHubRepoRef(owner="o", repo="r"),
        issue_number=1,
        body="body",
        execute=True,
        allow_comment=False,
    ) is None


def test_post_issue_comment_issue_number_none_returns_none() -> None:
    assert post_issue_comment_if_allowed(
        client=FakeGitHubClient(),
        repo=GitHubRepoRef(owner="o", repo="r"),
        issue_number=None,
        body="body",
        execute=True,
        allow_comment=True,
    ) is None


def test_post_issue_comment_calls_fake_client_when_allowed() -> None:
    client = FakeGitHubClient()
    post_issue_comment_if_allowed(
        client=client,
        repo=GitHubRepoRef(owner="o", repo="r"),
        issue_number=1,
        body="body",
        execute=True,
        allow_comment=True,
    )

    assert client.comments[0]["issue_number"] == 1


def test_sanitize_comment_body_removes_diff_tokens_and_paths() -> None:
    body = sanitize_comment_body("diff --git a b\nsecret ghp_token\n/path/to/file\n")

    assert "diff --git" not in body
    assert "ghp_token" not in body
    assert "/path/to/file" not in body


def test_extract_issue_number_reads_issue_json_number(tmp_path: Path) -> None:
    path = tmp_path / "issue.json"
    path.write_text(json.dumps({"number": 7}), encoding="utf-8")

    assert extract_issue_number({}, issue_json_path=path) == 7


def test_extract_issue_number_reads_issue_url(tmp_path: Path) -> None:
    path = tmp_path / "issue.json"
    path.write_text(json.dumps({"ref": {"url": "https://github.com/o/r/issues/12"}}), encoding="utf-8")

    assert extract_issue_number({}, issue_json_path=path) == 12


def test_build_issue_comment_body_contains_expected_summary() -> None:
    body = build_issue_comment_body(
        pr_url=None,
        run_id="issue-test",
        safety_summary="pass",
        artifact_summary=["auto_pr_plan.md", "auto_pr_manifest.json", "pr_body.md"],
        dry_run=True,
    )

    assert "Run ID: issue-test" in body


def test_build_issue_comment_body_preserves_pr_url() -> None:
    body = build_issue_comment_body(
        pr_url="https://github.com/o/r/pull/123",
        run_id="issue-test",
        safety_summary="pass",
        artifact_summary=["auto_pr_plan.md"],
        dry_run=False,
    )

    assert "https://github.com/o/r/pull/123" in body


def test_sanitize_comment_body_redacts_local_absolute_paths_but_not_urls() -> None:
    body = sanitize_comment_body(
        "diff --git a b\n"
        "ghp_secret\n"
        "/tmp/codepilot/x\n"
        "/home/user/repo\n"
        "https://github.com/o/r/pull/123\n"
    )

    assert "[REDACTED_PATH]" in body
    assert "https://github.com/o/r/pull/123" in body
    assert "diff --git" not in body
    assert "ghp_secret" not in body
