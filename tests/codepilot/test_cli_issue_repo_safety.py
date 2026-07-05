from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app


runner = CliRunner()


def _write_bug_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def _issue(tmp_path: Path) -> Path:
    path = tmp_path / "issue.md"
    path.write_text("# Add bug\n\nPlease fix add().\n", encoding="utf-8")
    return path


def test_cli_dirty_fail_returns_non_zero_and_writes_core_artifacts(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["issue", "--issue-file", str(_issue(tmp_path)), "--repo", str(repo), "--runs-dir", str(tmp_path / "runs"), "--run-id", "issue-fail", "--dirty-policy", "fail", "--overwrite"],
    )

    assert result.exit_code != 0
    assert "Repo safety denied" in result.stderr
    assert (tmp_path / "runs" / "issue-fail" / "issue.json").exists()
    assert (tmp_path / "runs" / "issue-fail" / "artifact_manifest.json").exists()


def test_cli_warn_can_continue_and_prints_manifest_and_restore_plan(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(_issue(tmp_path)),
            "--repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-warn",
            "--dirty-policy",
            "warn",
            "--fake-actions",
            str(fixture),
            "--approve",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    assert "Manifest:" in result.stdout
    assert "Restore plan:" in result.stdout
    assert "Repository is clean." not in result.stderr


def test_cli_worktree_outputs_enabled_and_preserves_original_repo(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(_issue(tmp_path)),
            "--repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-worktree",
            "--worktree",
            "--worktree-base-dir",
            str(tmp_path / "worktrees"),
            "--fake-actions",
            str(fixture),
            "--approve",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    assert "Worktree: enabled" in result.stdout
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_cli_cleanup_worktree_without_worktree_fails(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)

    result = runner.invoke(app, ["issue", "--issue-file", str(_issue(tmp_path)), "--repo", str(repo), "--cleanup-worktree"])

    assert result.exit_code != 0


def test_cli_worktree_base_dir_inside_repo_fails(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)

    result = runner.invoke(
        app,
        ["issue", "--issue-file", str(_issue(tmp_path)), "--repo", str(repo), "--worktree", "--worktree-base-dir", str(repo / "nested")],
    )

    assert result.exit_code != 0


def test_cli_no_manifest_and_no_restore_plan_warn(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fake_actions = tmp_path / "partial.jsonl"
    fake_actions.write_text('{"type":"finish","status":"partial","summary":"done"}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(_issue(tmp_path)),
            "--repo",
            str(repo),
            "--no-manifest",
            "--no-restore-plan",
            "--fake-actions",
            str(fake_actions),
        ],
    )

    assert "--no-manifest" in result.stderr
    assert "--no-restore-plan" in result.stderr


def test_cli_protected_env_dirty_returns_non_zero(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["issue", "--issue-file", str(_issue(tmp_path)), "--repo", str(repo), "--dirty-policy", "warn"],
    )

    assert result.exit_code != 0


def test_cli_artifacts_do_not_contain_secret_token(tmp_path: Path, monkeypatch) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_secret")

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(_issue(tmp_path)),
            "--repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-secret",
            "--fake-actions",
            str(fixture),
            "--approve",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    for path in (tmp_path / "runs" / "issue-secret").glob("*"):
        if path.is_file():
            assert "ghp_test_secret" not in path.read_text(encoding="utf-8", errors="ignore")


def test_cli_redact_absolute_paths_hides_paths_in_summary_and_restore_plan(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(_issue(tmp_path)),
            "--repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-redacted",
            "--fake-actions",
            str(fixture),
            "--approve",
            "--redact-absolute-paths",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    run_dir = tmp_path / "runs" / "issue-redacted"
    assert str(tmp_path) not in (run_dir / "pr_summary.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in (run_dir / "restore_plan.md").read_text(encoding="utf-8")


def test_cli_protected_after_path_denied_exits_non_zero(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fake_actions = tmp_path / "actions-shell.jsonl"
    fake_actions.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call",
                        "tool_name": "run_shell",
                        "arguments": {
                            "command": 'python -c "from pathlib import Path; Path(\'.\'+\'env\').write_text(\'SECRET=1\\\\n\', encoding=\'utf-8\')"'
                        },
                    }
                ),
                json.dumps({"type": "finish", "status": "partial", "summary": "done"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(_issue(tmp_path)),
            "--repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-protected-after",
            "--fake-actions",
            str(fake_actions),
            "--approve",
            "--overwrite",
        ],
    )

    assert result.exit_code != 0
    assert "protected_after_path_denied" in result.stdout
