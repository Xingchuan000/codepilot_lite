from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.pr_feedback.branch_update import push_pr_branch_update_if_allowed
from codepilot.pr_feedback.models import PRFeedbackStaleHeadError, PRRef


def test_branch_update_noop_does_not_check_remote_when_execute_false(tmp_path: Path) -> None:
    result = push_pr_branch_update_if_allowed(
        repo_path=tmp_path / "missing-repo",
        pr=PRRef(owner="o", repo="r", pull_number=1, url="https://example.com", head_branch="codepilot/test", base_branch="main"),
        new_commit_sha="abc123",
        expected_current_head_sha="abc123",
        execute=False,
        allow_push_update=True,
    )

    assert result == {"pushed": False, "reason": "execute=false"}


def test_branch_update_noop_does_not_check_remote_when_allow_push_update_false(tmp_path: Path) -> None:
    result = push_pr_branch_update_if_allowed(
        repo_path=tmp_path / "missing-repo",
        pr=PRRef(owner="o", repo="r", pull_number=1, url="https://example.com", head_branch="codepilot/test", base_branch="main"),
        new_commit_sha="abc123",
        expected_current_head_sha="abc123",
        execute=True,
        allow_push_update=False,
    )

    assert result == {"pushed": False, "reason": "allow_push_update=false"}


def test_branch_update_rejects_non_codepilot_branch() -> None:
    with pytest.raises(PRFeedbackStaleHeadError):
        push_pr_branch_update_if_allowed(
            repo_path=Path("missing-repo"),
            pr=PRRef(owner="o", repo="r", pull_number=1, url="https://example.com", head_branch="feature/test", base_branch="main"),
            new_commit_sha="abc123",
            expected_current_head_sha="abc123",
            execute=True,
            allow_push_update=True,
        )
