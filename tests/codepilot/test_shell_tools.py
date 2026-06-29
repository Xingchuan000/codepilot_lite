from pathlib import Path

from codepilot.tools.base import ToolRisk
from codepilot.tools.shell_tools import run_shell


def test_run_shell_success(tmp_path: Path) -> None:
    result = run_shell(tmp_path, command="python -c \"print('hello')\"")

    assert result.success is True
    assert result.output.strip() == "hello"
    assert result.output_summary == "Command succeeded with returncode 0."
    assert result.metadata["risk"] == ToolRisk.SHELL_EXECUTION.value


def test_run_shell_failure(tmp_path: Path) -> None:
    result = run_shell(tmp_path, command="python -c \"import sys; sys.exit(2)\"")

    assert result.success is False
    assert result.metadata["returncode"] == 2
    assert result.output_summary == "Command failed with returncode 2."


def test_run_shell_timeout(tmp_path: Path) -> None:
    result = run_shell(tmp_path, command="python -c \"import time; time.sleep(2)\"", timeout=1)

    assert result.success is False
    assert "timed out" in result.error
    assert result.metadata["returncode"] != 0


def test_run_shell_long_output_truncated(tmp_path: Path) -> None:
    result = run_shell(tmp_path, command="python -c \"print('x' * 20000)\"", max_output_chars=100)

    assert result.success is True
    assert result.metadata["truncated"] is True
    assert result.output.endswith("... truncated")


def test_run_shell_runs_in_repo_cwd(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("ok\n", encoding="utf-8")

    result = run_shell(tmp_path, command="python -c \"from pathlib import Path; print(Path.cwd().name)\"")

    assert result.success is True
    assert result.output.strip() == tmp_path.name


def test_run_shell_missing_repo(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = run_shell(missing, command="pwd")

    assert result.success is False
    assert "does not exist" in result.error


def test_run_shell_metadata_contains_returncode_and_duration(tmp_path: Path) -> None:
    result = run_shell(tmp_path, command="python -c \"print('ok')\"")

    assert result.metadata["returncode"] == 0
    assert result.metadata["duration_ms"] >= 0
