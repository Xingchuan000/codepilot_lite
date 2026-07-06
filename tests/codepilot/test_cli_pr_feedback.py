from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app
from tests.codepilot.test_pr_feedback_workflow import _write_manifest_bundle


runner = CliRunner()


def test_cli_pr_feedback_requires_exactly_one_locator() -> None:
    result = runner.invoke(app, ["pr-feedback", "--dry-run", "--overwrite"])

    assert result.exit_code != 0

    result = runner.invoke(app, ["pr-feedback", "--run-dir", "runs/a", "--run-id", "a", "--dry-run", "--overwrite"])

    assert result.exit_code != 0


def test_cli_pr_feedback_missing_manifest_returns_nonzero_and_writes_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    run_dir = tmp_path / "runs" / "empty-run"
    run_dir.mkdir(parents=True)

    result = runner.invoke(app, ["pr-feedback", "--run-dir", str(run_dir), "--overwrite"])

    assert result.exit_code != 0
    assert (run_dir / "ci_feedback_manifest.json").exists()
    assert "blocked" in result.stdout
    assert "GITHUB_TOKEN" not in result.stdout
    assert "GITHUB_TOKEN" not in result.stderr


def test_cli_pr_feedback_dry_run_without_token_on_valid_manifest_returns_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="abc123", current_head_sha="abc123")

    result = runner.invoke(app, ["pr-feedback", "--run-dir", str(run_dir), "--dry-run", "--overwrite"])

    assert result.exit_code == 0
    assert "Run ID: issue-test" in result.stdout
    assert (run_dir / "ci_feedback_manifest.json").exists()


def test_cli_pr_feedback_run_id_success_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="abc123", current_head_sha="abc123")

    result = runner.invoke(app, ["pr-feedback", "--run-id", "issue-test", "--runs-dir", str(tmp_path / "runs"), "--dry-run", "--overwrite"])

    assert result.exit_code == 0
    assert "Run ID: issue-test" in result.stdout
    assert (run_dir / "ci_feedback_manifest.json").exists()


def test_cli_pr_feedback_no_dry_run_requires_execute() -> None:
    result = runner.invoke(app, ["pr-feedback", "--run-dir", "runs/a", "--no-dry-run"])

    assert result.exit_code != 0
    assert "--no-dry-run requires --execute" in result.stderr


def test_cli_pr_feedback_allow_push_requires_allow_run_agent(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)

    result = runner.invoke(app, ["pr-feedback", "--run-dir", str(run_dir), "--allow-push-update"])

    assert result.exit_code != 0
    assert "--allow-push-update requires --allow-run-agent" in result.stderr
