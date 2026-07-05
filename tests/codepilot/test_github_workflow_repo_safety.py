from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codepilot.github.workflow import run_issue_workflow


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


def _write_issue(tmp_path: Path) -> Path:
    path = tmp_path / "issue.md"
    path.write_text("# Add bug\n\nPlease fix add().\n", encoding="utf-8")
    return path


def _success_fixture() -> Path:
    return Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()


def test_clean_repo_writes_manifest_restore_plan_and_summary_sections(tmp_path: Path) -> None:
    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=_write_bug_repo(tmp_path),
        run_id="issue-clean",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        overwrite=True,
    )

    assert result.success is True
    assert result.manifest_path is not None and result.manifest_path.exists()
    assert result.restore_plan_path is not None and result.restore_plan_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["safety_summary"]["baseline_dirty"] is False
    assert "src/calc.py" in manifest["patch"]["changed_files"]
    summary = result.pr_summary_path.read_text(encoding="utf-8") if result.pr_summary_path is not None else ""
    assert "## Safety" in summary
    assert "## Patch Metadata" in summary
    assert "Manifest" in summary
    assert "Restore plan" in summary


def test_manifest_records_pr_summary_and_manifest_as_existing_after_success(tmp_path: Path) -> None:
    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=_write_bug_repo(tmp_path),
        run_id="issue-manifest-success",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        overwrite=True,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    artifacts = {item["name"]: item for item in manifest["artifacts"]}
    assert artifacts["pr_summary"]["exists"] is True
    assert artifacts["artifact_manifest"]["exists"] is True
    assert artifacts["issue_json"]["exists"] is True
    assert artifacts["trace"]["exists"] is True
    assert artifacts["report_md"]["exists"] is True
    assert artifacts["report_json"]["exists"] is True
    assert artifacts["patch"]["exists"] is True
    assert artifacts["restore_plan"]["exists"] is True


def test_dirty_repo_fail_denies_before_agent_run(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-dirty-fail",
        runs_dir=tmp_path / "runs",
        dirty_policy="fail",
        overwrite=True,
    )

    assert result.success is False
    assert result.status == "repo_safety_denied"
    assert result.trace_path is None or not result.trace_path.exists()
    assert result.patch_path is None
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert result.pr_summary_path is not None and result.pr_summary_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert manifest["safety_decision"] == "deny"


def test_manifest_records_pr_summary_and_manifest_as_existing_after_repo_safety_denied(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-manifest-deny",
        runs_dir=tmp_path / "runs",
        dirty_policy="fail",
        overwrite=True,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    artifacts = {item["name"]: item for item in manifest["artifacts"]}
    assert artifacts["issue_json"]["exists"] is True
    assert artifacts["pr_summary"]["exists"] is True
    assert artifacts["restore_plan"]["exists"] is True
    assert artifacts["artifact_manifest"]["exists"] is True
    assert artifacts["trace"]["exists"] is False
    assert artifacts["report_md"]["exists"] is False
    assert artifacts["report_json"]["exists"] is False
    assert artifacts["patch"]["exists"] is False


def test_dirty_repo_warn_continues_and_records_preexisting_warning(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-dirty-warn",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        dirty_policy="warn",
        overwrite=True,
    )

    assert result.success is True
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert manifest["safety_summary"]["baseline_dirty"] is True
    assert manifest["safety_summary"]["contains_preexisting_changes"] is True
    assert "may include changes that existed before CodePilot started" in result.pr_summary_path.read_text(encoding="utf-8")


def test_worktree_isolation_keeps_original_repo_clean(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-worktree",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        worktree=True,
        worktree_base_dir=tmp_path / "worktrees-1",
        overwrite=True,
    )

    assert result.used_worktree is True
    assert result.effective_repo_path != result.repo_path
    assert result.worktree_path is not None and result.worktree_path.exists()
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert (result.worktree_path / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert "+    return a + b" in result.patch_path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert manifest["used_worktree"] is True
    assert "Worktree used: yes" in result.pr_summary_path.read_text(encoding="utf-8")


def test_dirty_repo_worktree_does_not_include_original_dirty_file(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-dirty-worktree",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        worktree=True,
        dirty_policy="fail",
        worktree_base_dir=tmp_path / "worktrees-2",
        overwrite=True,
    )

    assert result.success is True
    assert "README.md" not in result.patch_path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert manifest["safety_summary"]["baseline_dirty"] is True
    assert manifest["used_worktree"] is True
    assert "Original repo had uncommitted changes" in result.pr_summary_path.read_text(encoding="utf-8")


def test_require_clean_source_for_worktree_denies(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-worktree-clean-required",
        runs_dir=tmp_path / "runs",
        worktree=True,
        worktree_base_dir=tmp_path / "worktrees-3",
        require_clean_source_for_worktree=True,
        overwrite=True,
    )

    assert result.success is False
    assert result.status == "repo_safety_denied"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert manifest["safety_decision"] == "deny"


def test_protected_dirty_path_denies(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-protected",
        runs_dir=tmp_path / "runs",
        dirty_policy="warn",
        overwrite=True,
    )

    assert result.success is False
    assert result.status == "repo_safety_denied"
    assert "protected dirty path" in result.pr_summary_path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert ".env" in manifest["safety_summary"]["protected_dirty_files"]


def test_cleanup_worktree_records_cleanup_result(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-worktree-cleanup",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        worktree=True,
        cleanup_worktree=True,
        worktree_base_dir=tmp_path / "worktrees-4",
        overwrite=True,
    )

    assert result.patch_path is not None and result.patch_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path is not None else {}
    assert manifest["cleanup"]["requested"] is True
    assert manifest["cleanup"]["branch_left_in_place"] is True
    assert "Worktree branch:" in result.restore_plan_path.read_text(encoding="utf-8")


def test_issue_workflow_does_not_modify_github_workflow_path_via_approved_edit(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    (repo / ".github" / "workflows").mkdir(parents=True)
    workflow_path = repo / ".github" / "workflows" / "ci.yml"
    workflow_path.write_text("name: ci\n", encoding="utf-8")
    subprocess.run(["git", "add", ".github/workflows/ci.yml"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add workflow"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    fake_actions = tmp_path / "actions.jsonl"
    fake_actions.write_text(
        '\n'.join(
            [
                '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":".github/workflows/ci.yml","start_line":1,"end_line":1,"replacement":"changed\\n"}}',
                '{"type":"finish","status":"partial","summary":"done"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-workflow-protect",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=fake_actions,
        overwrite=True,
    )

    assert workflow_path.read_text(encoding="utf-8") == "name: ci\n"
    assert result.report_path is not None and "policy" in result.report_path.read_text(encoding="utf-8").lower()
    assert ".github/workflows/ci.yml" not in result.patch_path.read_text(encoding="utf-8")


def test_issue_workflow_writes_failure_artifacts_when_agent_run_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _write_bug_repo(tmp_path)

    def fake_raise(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("codepilot.github.workflow.run_agent_task", fake_raise)

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-agent-fail",
        runs_dir=tmp_path / "runs",
        overwrite=True,
    )

    assert result.status == "agent_run_failed"
    assert result.success is False
    assert result.issue_json_path.exists()
    assert result.pr_summary_path.exists()
    assert result.manifest_path.exists()
    assert result.restore_plan_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "agent_run_failed"


def test_issue_workflow_writes_failure_artifacts_when_patch_export_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _write_bug_repo(tmp_path)

    def fake_patch_export(*args, **kwargs):
        raise RuntimeError("patch failed")

    monkeypatch.setattr("codepilot.github.workflow.export_patch_with_metadata", fake_patch_export)

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-patch-fail",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=_success_fixture(),
        overwrite=True,
    )

    assert result.status == "patch_export_failed"
    assert result.success is False
    assert result.report_path is not None and result.report_path.exists()
    assert result.pr_summary_path.exists()
    assert result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "patch_export_failed"
    artifacts = {item["name"]: item for item in manifest["artifacts"]}
    assert artifacts["patch"]["exists"] is False


def test_issue_workflow_marks_protected_after_path_denied_when_shell_creates_env(tmp_path: Path) -> None:
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

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-protected-after",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=fake_actions,
        overwrite=True,
    )

    assert result.status == "protected_after_path_denied"
    assert result.success is False
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert ".env" in manifest["safety_summary"]["protected_after_files"]
    assert "Protected dirty files after run" in result.pr_summary_path.read_text(encoding="utf-8")
    assert ".env" not in result.patch_path.read_text(encoding="utf-8")
    assert "SECRET=1" not in result.patch_path.read_text(encoding="utf-8")


def test_issue_workflow_records_untracked_normal_file_in_manifest_and_summary(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fake_actions = tmp_path / "actions-untracked.jsonl"
    fake_actions.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call",
                        "tool_name": "run_shell",
                        "arguments": {
                            "command": 'python -c "from pathlib import Path; Path(\'created.py\').write_text(\'print(1)\\\\n\', encoding=\'utf-8\')"'
                        },
                    }
                ),
                json.dumps({"type": "finish", "status": "partial", "summary": "done"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_issue_workflow(
        issue_file=_write_issue(tmp_path),
        repo=repo,
        run_id="issue-untracked",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=fake_actions,
        overwrite=True,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert "created.py" in manifest["safety_summary"]["untracked_files"]
    assert "Untracked files:" in result.pr_summary_path.read_text(encoding="utf-8")
