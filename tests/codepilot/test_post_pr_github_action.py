from __future__ import annotations

from codepilot.post_pr.github_action import render_post_pr_automation_workflow_template, write_post_pr_automation_workflow_template


def test_workflow_template_contains_dispatch_and_permissions() -> None:
    text = render_post_pr_automation_workflow_template()
    assert "workflow_dispatch" in text
    assert "permissions: {}" in text
    assert "concurrency:" in text
    assert "codepilot-post-pr-${{ inputs.run_id }}" in text
    assert "INPUT_RUN_ID" in text
    assert "INPUT_MAX_ROUNDS" in text
    assert "INPUT_APPROVE_COMMENT" in text
    assert "INPUT_APPROVE_RUN_AGENT" in text
    assert "INPUT_APPROVE_PUSH_UPDATE" in text
    assert "args+=(--approve-comment)" in text
    assert "args+=(--approve-run-agent)" in text
    assert "args+=(--approve-push-update)" in text
    assert "path: runs/${{ inputs.run_id }}/post_pr" in text


def test_workflow_template_write(tmp_path) -> None:
    path = write_post_pr_automation_workflow_template(tmp_path / "workflow.yml", overwrite=True)
    assert path.exists()
