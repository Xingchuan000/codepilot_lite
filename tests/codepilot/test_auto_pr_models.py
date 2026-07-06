from __future__ import annotations

from pathlib import Path

from codepilot.auto_pr.models import (
    AutoPRInput,
    AutoPRResult,
    AutoPRSafetyGate,
    GitHubRepoRef,
    PRCreateRequest,
    to_auto_pr_jsonable,
)


def test_auto_pr_input_defaults() -> None:
    value = AutoPRInput(run_id="run-1", run_dir=Path("runs/run-1"), pr_assist_manifest_path=Path("x.json"))

    assert value.dry_run is True
    assert value.execute is False
    assert value.allow_push is False
    assert value.allow_create_pr is False
    assert value.allow_comment is False


def test_auto_pr_result_defaults() -> None:
    value = AutoPRResult(
        run_id="run-1",
        run_dir=Path("runs/run-1"),
        status="planned",
        safety_gate=AutoPRSafetyGate(status="pass"),
    )

    assert value.push_executed is False
    assert value.pr_created is False
    assert value.github_api_called is False
    assert value.comment_posted is False


def test_to_auto_pr_jsonable_converts_path() -> None:
    assert to_auto_pr_jsonable(Path("a/b.txt")) == "a/b.txt"


def test_to_auto_pr_jsonable_converts_nested_dataclass() -> None:
    value = PRCreateRequest(
        repo=GitHubRepoRef(owner="o", repo="r"),
        title="title",
        body_path=Path("pr_body.md"),
        head_branch="codepilot/x",
        base_branch="main",
    )

    assert to_auto_pr_jsonable({"request": [value]}) == {
        "request": [
            {
                "repo": {"owner": "o", "repo": "r", "remote_url": None},
                "title": "title",
                "body_path": "pr_body.md",
                "head_branch": "codepilot/x",
                "base_branch": "main",
                "draft": True,
            }
        ]
    }


def test_pr_create_request_default_draft_true() -> None:
    value = PRCreateRequest(
        repo=GitHubRepoRef(owner="o", repo="r"),
        title="title",
        body_path=Path("pr_body.md"),
        head_branch="codepilot/x",
        base_branch="main",
    )

    assert value.draft is True
