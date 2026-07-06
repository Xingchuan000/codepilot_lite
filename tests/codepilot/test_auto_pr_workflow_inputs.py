from __future__ import annotations

import pytest

from codepilot.auto_pr.models import AutoPRWorkflowInputError
from codepilot.auto_pr.workflow_inputs import (
    validate_head_branch,
    validate_issue_url,
    validate_repo_slug,
    validate_run_id,
)


def test_validate_run_id_accepts_safe_value() -> None:
    assert validate_run_id("issue-test_1.2") == "issue-test_1.2"


@pytest.mark.parametrize(
    ("value",),
    [("",), ("../../x",), ("a/b",), ("a b",), ("x;rm",)],
)
def test_validate_run_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(AutoPRWorkflowInputError):
        validate_run_id(value)


def test_validate_issue_url_none_returns_none() -> None:
    assert validate_issue_url(None) is None


def test_validate_issue_url_accepts_github_issue() -> None:
    assert validate_issue_url("https://github.com/o/r/issues/1") == "https://github.com/o/r/issues/1"


def test_validate_issue_url_rejects_non_github_url() -> None:
    with pytest.raises(AutoPRWorkflowInputError):
        validate_issue_url("https://evil.test/o/r/issues/1")


def test_validate_repo_slug_accepts_owner_repo() -> None:
    assert validate_repo_slug("owner/repo") == "owner/repo"


def test_validate_repo_slug_rejects_extra_path() -> None:
    with pytest.raises(AutoPRWorkflowInputError):
        validate_repo_slug("owner/repo/extra")


def test_validate_head_branch_defaults_to_codepilot_run_id() -> None:
    assert validate_head_branch(None, run_id="issue-test") == "codepilot/issue-test"


def test_validate_head_branch_rejects_main() -> None:
    with pytest.raises(AutoPRWorkflowInputError):
        validate_head_branch("main", run_id="x")


def test_validate_head_branch_rewrites_feature_branch() -> None:
    assert validate_head_branch("feature/x", run_id="x") == "codepilot/feature-x"


def test_validate_head_branch_sanitizes_spaces() -> None:
    with pytest.raises(AutoPRWorkflowInputError):
        validate_head_branch("codepilot/my run", run_id="x")
