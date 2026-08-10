from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from codepilot.agent.runner import (
    ModelConfigurationRequired,
    build_codepilot_llm,
    resolve_litellm_config,
    run_agent_task,
)


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


def test_run_agent_task_with_fake_responses_fixes_demo_repo(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    fixture = Path("tests/codepilot/fixtures/agent_responses_success.jsonl").resolve()

    result = run_agent_task(
        task="Fix the failing add test",
        repo=repo,
        fake_responses=fixture,
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
    fixture = Path("tests/codepilot/fixtures/agent_responses_success.jsonl").resolve()

    result = run_agent_task(
        task="Fix the failing add test",
        repo=repo,
        fake_responses=fixture,
        policy_mode="read_only",
        runs_dir=tmp_path / "runs",
        run_id="run-test",
    )

    assert result.success is False
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_native_model_rejects_drop_params() -> None:
    with pytest.raises(ModelConfigurationRequired, match="drop_params=true"):
        build_codepilot_llm(
            model="openai/gpt-4o-mini",
            model_config=["model.model_kwargs.drop_params=true"],
        )


def test_native_model_is_not_blocked_by_stale_litellm_capability_metadata() -> None:
    built = build_codepilot_llm(model="deepseek/deepseek-v4-flash")

    assert built.client.__class__.__name__ == "LiteLLMNativeClient"


def test_native_model_is_constructed_from_litellm_config() -> None:
    config = resolve_litellm_config(
        model=None,
        model_config=["model.model_name=openai/gpt-4o-mini", "model.model_kwargs.temperature=0.2"],
        environ={},
    )

    built = build_codepilot_llm(model="openai/gpt-4o-mini", model_config=["model.model_kwargs.temperature=0.2"])

    assert config.model_name == "openai/gpt-4o-mini"
    assert config.model_kwargs == {"temperature": 0.2}
    assert built.client.__class__.__name__ == "LiteLLMNativeClient"


def test_native_model_loads_minisweagent_global_config(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / ".env"
    config_file.write_text("CODEPILOT_TEST_API_KEY=from-minisweagent\n", encoding="utf-8")
    monkeypatch.delenv("CODEPILOT_TEST_API_KEY", raising=False)
    monkeypatch.setattr("minisweagent.global_config_file", config_file)

    build_codepilot_llm(model="openai/gpt-4o-mini")

    assert os.getenv("CODEPILOT_TEST_API_KEY") == "from-minisweagent"


@pytest.mark.parametrize(
    ("model_name",),
    [("openai/test-model",), ("anthropic/test-model",), ("gemini/test-model",), ("deepseek/test-model",)],
)
def test_provider_matrix_builds_the_same_native_client(model_name: str) -> None:
    built = build_codepilot_llm(model=model_name)

    assert built.client.__class__.__name__ == "LiteLLMNativeClient"
    assert built.model == model_name
