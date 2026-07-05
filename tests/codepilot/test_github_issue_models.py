from __future__ import annotations

from pydantic import ValidationError

from codepilot.github.issue_models import IssueRef, IssueTask


def test_issue_ref_file_source_is_valid() -> None:
    issue_ref = IssueRef(source="file", file_path="issue.md")

    assert issue_ref.source == "file"
    assert issue_ref.file_path == "issue.md"


def test_issue_ref_github_source_is_valid() -> None:
    issue_ref = IssueRef(
        source="github",
        url="https://github.com/openai/codex/issues/1",
        owner="openai",
        repo="codex",
        number=1,
    )

    assert issue_ref.source == "github"
    assert issue_ref.number == 1


def test_issue_task_labels_default_is_not_shared() -> None:
    first = IssueTask(title="a", body="b", ref=IssueRef(source="file", file_path="a.md"))
    second = IssueTask(title="a", body="b", ref=IssueRef(source="file", file_path="b.md"))

    first.labels.append("bug")

    assert second.labels == []


def test_issue_task_metadata_default_is_not_shared() -> None:
    first = IssueTask(title="a", body="b", ref=IssueRef(source="file", file_path="a.md"))
    second = IssueTask(title="a", body="b", ref=IssueRef(source="file", file_path="b.md"))

    first.metadata["state"] = "open"

    assert second.metadata == {}


def test_issue_task_model_dump_json_does_not_include_token_fields() -> None:
    data = IssueTask(
        title="a",
        body="b",
        ref=IssueRef(source="file", file_path="a.md"),
    ).model_dump(mode="json")

    assert "token" not in data
    assert "github_token" not in data
    assert "authorization" not in data


def test_issue_ref_rejects_unknown_source() -> None:
    try:
        IssueRef(source="jira")
    except ValidationError:
        return
    raise AssertionError("IssueRef(source='jira') should raise ValidationError")
