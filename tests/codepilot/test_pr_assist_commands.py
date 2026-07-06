from codepilot.pr_assist.commands import render_manual_pr_commands
from codepilot.pr_assist.models import PRAssistSafetyGate


def test_render_manual_pr_commands_for_pass_contains_expected_steps() -> None:
    plan = render_manual_pr_commands(
        run_id="issue-test",
        repo_path="/tmp/repo",
        effective_repo_path="/tmp/repo",
        run_dir="/tmp/runs/issue-test",
        patch_path="/tmp/runs/issue-test/changes.patch",
        branch_name="codepilot/issue-test",
        safety_gate=PRAssistSafetyGate(status="pass"),
        redact_absolute_paths=True,
    )

    text = "\n".join(plan.commands)
    assert "apply --check" in text
    assert "switch -c" in text
    assert "apply --index" in text
    assert "commit -m" in text
    assert "git push" not in text
    assert "gh pr create" not in text
    assert "<repo>" in text
    assert "/tmp/" not in text


def test_render_manual_pr_commands_for_fail_is_review_only() -> None:
    plan = render_manual_pr_commands(
        run_id="issue-test",
        repo_path="/tmp/repo",
        effective_repo_path="/tmp/repo",
        run_dir="/tmp/runs/issue-test",
        patch_path="/tmp/runs/issue-test/changes.patch",
        branch_name="codepilot/issue-test",
        safety_gate=PRAssistSafetyGate(status="fail"),
    )

    text = "\n".join(plan.commands)
    assert "apply --check" not in text
    assert "apply --index" not in text
    assert "commit -m" not in text


def test_render_manual_pr_commands_optional_gh_pr_is_comment_only() -> None:
    plan = render_manual_pr_commands(
        run_id="issue-test",
        repo_path="/tmp/repo",
        effective_repo_path="/tmp/repo",
        run_dir="/tmp/runs/issue-test",
        patch_path="/tmp/runs/issue-test/changes.patch",
        branch_name="codepilot/issue-test",
        safety_gate=PRAssistSafetyGate(status="pass"),
        include_gh_pr_command=True,
    )

    text = "\n".join(plan.commands)
    assert "# gh pr create" in text
    assert "reset --hard" not in text
    assert "clean -fd" not in text
    assert "stash --include-untracked" not in text
    assert "checkout -- ." not in text
