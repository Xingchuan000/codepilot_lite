from pathlib import Path

from codepilot.pr_assist.github_action import render_github_action_template, write_github_action_template


def test_render_github_action_template_contains_read_only_defaults() -> None:
    content = render_github_action_template()

    assert "workflow_dispatch" in content
    assert "contents: read" in content
    assert "issues: read" in content
    assert "pull-requests: read" in content
    assert "persist-credentials: false" in content
    assert "git push" not in content
    assert "gh pr create" not in content
    assert "GITHUB_TOKEN" not in content
    assert "ghp_" not in content
    assert "github_pat_" not in content


def test_write_github_action_template_writes_only_target_file(tmp_path: Path) -> None:
    path = write_github_action_template(tmp_path / "runs" / "issue-test" / "github_action_template.yml")

    assert path.exists()
    assert not (tmp_path / ".github" / "workflows").exists()
