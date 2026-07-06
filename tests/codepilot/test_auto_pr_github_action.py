from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.auto_pr.github_action import (
    render_controlled_auto_pr_workflow_template,
    write_controlled_auto_pr_workflow_template,
)


def test_render_controlled_auto_pr_workflow_template_contains_expected_guards() -> None:
    text = render_controlled_auto_pr_workflow_template()

    assert "workflow_dispatch" in text
    assert "pull_request_target" not in text
    assert "issue_comment" not in text
    assert "permissions: {}" in text
    assert "contents: read" in text
    assert "contents: write" in text
    assert "issues: write" in text
    assert "pull-requests: write" in text
    assert "inputs.dry_run != 'true' && inputs.create_pr == 'true'" in text
    assert 'default: "true"' in text
    assert 'default: "false"' in text
    assert "GITHUB_TOKEN" in text
    assert "upload-artifact" in text
    assert "persist-credentials: false" in text
    assert "persist-credentials: true" in text


def test_render_controlled_auto_pr_workflow_template_uses_env_before_run() -> None:
    text = render_controlled_auto_pr_workflow_template()

    assert "CODEPILOT_ISSUE_URL" in text
    assert 'issue "$CODEPILOT_ISSUE_URL"' in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text


def test_write_controlled_auto_pr_workflow_template_writes_file(tmp_path: Path) -> None:
    path = write_controlled_auto_pr_workflow_template(tmp_path / "workflow.yml")

    assert path.exists()


def test_write_controlled_auto_pr_workflow_template_honors_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text("old\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_controlled_auto_pr_workflow_template(path, overwrite=False)
