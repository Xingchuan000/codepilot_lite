from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app
from tests.codepilot.test_auto_pr_workflow import _init_repo, _write_run_dir


runner = CliRunner()


def test_cli_auto_pr_run_dir_dry_run_success(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--overwrite", "--repo-slug", "o/r"])

    assert result.exit_code == 0
    assert "Controlled Auto PR plan generated." in result.stdout


def test_cli_auto_pr_invalid_head_branch_returns_nonzero_without_writing_plan(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(
        app,
        [
            "auto-pr",
            "--run-dir",
            str(run_dir),
            "--overwrite",
            "--repo-slug",
            "o/r",
            "--head-branch",
            "codepilot/bad branch",
        ],
    )

    assert result.exit_code != 0
    assert not (run_dir / "auto_pr_plan.md").exists()
    assert not (run_dir / "auto_pr_manifest.json").exists()


def test_cli_auto_pr_run_id_dry_run_success(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["auto-pr", "--run-id", "issue-test", "--runs-dir", str(tmp_path / "runs"), "--overwrite", "--repo-slug", "o/r"])

    assert result.exit_code == 0
    assert "Run ID: issue-test" in result.stdout


def test_cli_auto_pr_requires_exactly_one_locator(tmp_path: Path) -> None:
    assert runner.invoke(app, ["auto-pr"]).exit_code != 0
    assert runner.invoke(app, ["auto-pr", "--run-dir", str(tmp_path), "--run-id", "x"]).exit_code != 0


def test_cli_auto_pr_defaults_print_no_side_effects(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--overwrite", "--repo-slug", "o/r"])

    assert "Push executed: no" in result.stdout
    assert "PR created: no" in result.stdout


def test_cli_auto_pr_execute_without_allow_push_returns_non_zero(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    assert runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--execute", "--overwrite", "--repo-slug", "o/r"]).exit_code != 0


def test_cli_auto_pr_safety_fail_execute_returns_non_zero(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_status="fail")

    assert runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--execute", "--allow-push", "--overwrite", "--repo-slug", "o/r"]).exit_code != 0


def test_cli_auto_pr_safety_fail_dry_run_returns_zero_and_shows_blockers(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_status="fail")

    result = runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--overwrite", "--repo-slug", "o/r"])

    assert result.exit_code == 0
    assert "planned_with_blockers" in result.stdout


def test_cli_auto_pr_controlled_action_template_generates_workflow(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--overwrite", "--repo-slug", "o/r"])

    assert (run_dir / "controlled_auto_pr_workflow.yml").exists()


def test_cli_auto_pr_no_controlled_action_template_skips_workflow(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--overwrite", "--repo-slug", "o/r", "--no-controlled-action-template"])

    assert result.exit_code == 0
    assert not (run_dir / "controlled_auto_pr_workflow.yml").exists()


def test_cli_auto_pr_overwrite_false_protects_existing_artifacts(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    (run_dir / "auto_pr_plan.md").write_text("old\n", encoding="utf-8")

    assert runner.invoke(app, ["auto-pr", "--run-dir", str(run_dir), "--repo-slug", "o/r"]).exit_code != 0


def test_cli_auto_pr_allow_create_pr_without_token_returns_nonzero_without_push(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, remote = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = runner.invoke(
        app,
        [
            "auto-pr",
            "--run-dir",
            str(run_dir),
            "--execute",
            "--allow-push",
            "--allow-create-pr",
            "--overwrite",
            "--repo-slug",
            "o/r",
        ],
    )

    assert result.exit_code != 0
    assert "GITHUB_TOKEN" not in (result.stdout + result.stderr)
    assert "Push executed: no" in result.stdout
    assert "GitHub API called: no" in result.stdout
    remote_branches = __import__("subprocess").run(
        ["git", "ls-remote", "--heads", str(remote), "codepilot/issue-test"],
        check=True,
        stdout=__import__("subprocess").PIPE,
        stderr=__import__("subprocess").PIPE,
        text=True,
    ).stdout.strip()
    assert remote_branches == ""
