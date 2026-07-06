from __future__ import annotations

from pathlib import Path

from codepilot.post_pr.models import PostPRAutomationResult, PostPRRoundRef
from codepilot.post_pr.report import render_post_pr_automation_report, write_post_pr_automation_report


def test_report_contains_manual_next_command_and_round_table(tmp_path: Path) -> None:
    result = PostPRAutomationResult(
        run_id="r",
        run_dir=tmp_path / "run",
        post_pr_dir=tmp_path / "run/post_pr",
        status="awaiting_approval",
        terminal_reason="awaiting_approval",
        rounds=[PostPRRoundRef(round_id="round-001", round_index=1, round_dir=tmp_path / "run/post_pr/round-001")],
    )
    text = render_post_pr_automation_report(result)
    assert "Manual Next Command" in text
    assert "round-001" in text
    path = write_post_pr_automation_report(result, tmp_path / "report.md", overwrite=True)
    assert path.exists()

