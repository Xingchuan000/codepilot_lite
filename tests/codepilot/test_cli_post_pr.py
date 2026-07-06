from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from codepilot.cli import app


runner = CliRunner()


def test_cli_post_pr_help() -> None:
    assert runner.invoke(app, ["post-pr", "--help"]).exit_code == 0


def test_cli_post_pr_requires_locator() -> None:
    assert runner.invoke(app, ["post-pr"]).exit_code != 0


def test_cli_post_pr_uses_run_dir(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    (run_dir / "auto_pr_manifest.json").write_text('{"schema_version":"codepilot.auto_pr_manifest.v1","run_id":"issue-001"}', encoding="utf-8")
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "codepilot.cli.run_post_pr_automation",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(
            run_id="issue-001",
            status="awaiting_approval",
            terminal_reason="awaiting_approval",
            rounds=[],
            approval_request_path=run_dir / "post_pr/approval_request.md",
            manifest_path=run_dir / "post_pr/post_pr_automation_manifest.json",
            report_path=run_dir / "post_pr/post_pr_automation_report.md",
            workflow_path=None,
        ),
    )
    result = runner.invoke(app, ["post-pr", "--run-dir", str(run_dir), "--overwrite"])
    assert result.exit_code == 0
    assert "Post-PR automation completed." in result.stdout
    assert calls[0]["dry_run"] is True


def test_cli_post_pr_execute_disables_effective_dry_run(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    (run_dir / "auto_pr_manifest.json").write_text('{"schema_version":"codepilot.auto_pr_manifest.v1","run_id":"issue-001"}', encoding="utf-8")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codepilot.cli.run_post_pr_automation",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(
            run_id="issue-001",
            status="awaiting_approval",
            terminal_reason="awaiting_approval",
            rounds=[],
            approval_request_path=run_dir / "post_pr/approval_request.md",
            manifest_path=run_dir / "post_pr/post_pr_automation_manifest.json",
            report_path=run_dir / "post_pr/post_pr_automation_report.md",
            workflow_path=None,
        ),
    )
    runner.invoke(app, ["post-pr", "--run-dir", str(run_dir), "--execute", "--approve-run-agent", "--overwrite"])
    assert calls[0]["dry_run"] is False
