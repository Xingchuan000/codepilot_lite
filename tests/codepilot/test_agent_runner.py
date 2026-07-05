from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.agent.runner import run_agent_task


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


def test_run_agent_task_with_fake_actions_fixes_demo_repo(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = run_agent_task(
        task="Fix the failing add test",
        repo=repo,
        fake_actions=fixture,
        approve=True,
        policy_mode="build",
        runs_dir=tmp_path / "runs",
        run_id="run-test",
    )

    assert result.status == "success"
    assert result.success is True
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert (tmp_path / "runs" / "run-test" / "trace.jsonl").exists()


def test_run_agent_task_read_only_does_not_modify_file(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = run_agent_task(
        task="Fix the failing add test",
        repo=repo,
        fake_actions=fixture,
        policy_mode="read_only",
        runs_dir=tmp_path / "runs",
        run_id="run-test",
    )

    assert result.success is False
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_run_agent_task_uses_swe_adapter_when_fake_actions_missing(tmp_path: Path, monkeypatch) -> None:
    repo = _write_bug_repo(tmp_path)
    calls = []

    class FakeModel:
        def query_without_default_tools(self, messages):
            calls.append(messages)
            return {"role": "assistant", "content": '{"type":"finish","status":"partial","summary":"done"}'}

    monkeypatch.setattr("codepilot.agent.runner.get_model", lambda config=None: FakeModel())

    result = run_agent_task(
        task="Inspect calc",
        repo=repo,
        model_config=['model.model_class="deterministic"'],
        runs_dir=tmp_path / "runs",
        run_id="run-test",
    )

    assert result.status == "partial"
    assert result.success is False
    assert len(calls) == 1
