from __future__ import annotations

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


def test_cli_agent_run_with_fake_actions(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = runner.invoke(
        app,
        [
            "agent-run",
            "Fix the failing add test",
            "--repo",
            str(repo),
            "--fake-actions",
            str(fixture),
            "--approve",
            "--policy-mode",
            "build",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 0
    assert "Status: success" in result.stdout
    assert "Steps: 6" in result.stdout
    assert "Trace:" in result.stdout
    assert (tmp_path / "runs" / "run-test" / "trace.jsonl").exists()


def test_cli_agent_run_with_alias_fake_actions(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_aliases.jsonl").resolve()

    result = runner.invoke(
        app,
        [
            "agent-run",
            "Fix the failing add test",
            "--repo",
            str(repo),
            "--fake-actions",
            str(fixture),
            "--approve",
            "--policy-mode",
            "build",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test-aliases",
        ],
    )

    assert result.exit_code == 0
    assert "Status: success" in result.stdout
    assert "Steps: 6" in result.stdout
    trace_path = tmp_path / "runs" / "run-test-aliases" / "trace.jsonl"
    assert trace_path.exists()
    assert "normalization_applied" in trace_path.read_text(encoding="utf-8")


def test_cli_agent_run_read_only_does_not_modify_file(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = runner.invoke(
        app,
        [
            "agent-run",
            "Fix the failing add test",
            "--repo",
            str(repo),
            "--fake-actions",
            str(fixture),
            "--policy-mode",
            "read_only",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert "Status:" in result.stdout
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_cli_agent_run_respects_max_steps(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    no_finish = tmp_path / "no_finish.jsonl"
    no_finish.write_text(
        '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}\n'
        '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}\n'
        '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent-run",
            "Inspect calc",
            "--repo",
            str(repo),
            "--fake-actions",
            str(no_finish),
            "--max-steps",
            "3",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 1
    assert "Status: max_steps_exceeded" in result.stdout


def test_cli_agent_run_uses_swe_adapter_path_when_fake_actions_missing(tmp_path: Path, monkeypatch) -> None:
    repo = _write_bug_repo(tmp_path)
    calls = []

    class FakeModel:
        def query_without_default_tools(self, messages):
            calls.append(messages)
            return {"role": "assistant", "content": '{"type":"finish","status":"partial","summary":"done"}'}

    monkeypatch.setattr("codepilot.cli.get_model", lambda config=None: FakeModel())

    result = runner.invoke(
        app,
        [
            "agent-run",
            "Inspect calc",
            "--repo",
            str(repo),
            "--model-config",
            'model.model_class="deterministic"',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 1
    assert "Status: partial" in result.stdout
    assert len(calls) == 1


def test_cli_agent_run_rejects_unknown_llm_flags() -> None:
    result = runner.invoke(app, ["agent-run", "task", "--llm-base-url", "http://example.com"])

    assert result.exit_code != 0
