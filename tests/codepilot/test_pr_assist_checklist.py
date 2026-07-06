from codepilot.pr_assist.checklist import render_review_checklist
from codepilot.pr_assist.models import PRBodyData, PRAssistSafetyGate


def _pr_body_data(*, tests: list[str], baseline_dirty: bool | None = False) -> PRBodyData:
    return PRBodyData(
        title="Demo",
        issue_ref="issue.md",
        summary="summary",
        changed_files=["src/calc.py"],
        tests=tests,
        safety_notes=[],
        report_path="report.json",
        patch_path="changes.patch",
        manifest_path="artifact_manifest.json",
        restore_plan_path="restore_plan.md",
        patch_sha256="abc",
        patch_empty=False,
        worktree_used=True,
        baseline_dirty=baseline_dirty,
    )


def test_render_review_checklist_contains_sections_and_warnings() -> None:
    content = render_review_checklist(
        manifest={"used_worktree": True, "patch": {"is_empty": False}, "safety_summary": {"baseline_dirty": True}},
        pr_body_data=_pr_body_data(tests=["unknown"], baseline_dirty=True),
        safety_gate=PRAssistSafetyGate(status="fail", reasons=["repo_safety_denied"], warnings=["Dirty files"]),
    )

    assert "## Patch Scope" in content
    assert "## Safety" in content
    assert "## Tests" in content
    assert "## Worktree / Cleanup" in content
    assert "## PR Body" in content
    assert "> [!WARNING]" in content
    assert "Run relevant tests manually" in content
    assert "Baseline was dirty." in content
    assert "Agent used an isolated worktree." in content
    assert "diff --git" not in content
