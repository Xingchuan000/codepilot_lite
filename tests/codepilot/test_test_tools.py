from pathlib import Path
import sys

from codepilot.tools.base import ToolRisk
from codepilot.tools.test_tools import normalize_test_command, run_tests


def _write_pytest_repo(tmp_path: Path, *, passing: bool) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    expected = "3" if passing else "4"
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == " + expected + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_run_tests_passes_pytest_command(tmp_path: Path) -> None:
    result = run_tests(_write_pytest_repo(tmp_path, passing=True), command="pytest -q")

    assert result.success is True
    assert result.metadata["status"] == "passed"
    assert result.metadata["returncode"] == 0
    assert result.metadata["original_command"] == "pytest -q"
    assert result.metadata["executed_command"] == f"{sys.executable} -m pytest -q"
    assert result.metadata["executed_argv"] == [sys.executable, "-m", "pytest", "-q"]


def test_run_tests_fails_and_returns_failed_tests(tmp_path: Path) -> None:
    result = run_tests(_write_pytest_repo(tmp_path, passing=False), command="pytest -q")

    assert result.success is False
    assert result.metadata["status"] == "failed"
    assert "tests/test_calc.py::test_add" in result.metadata["failed_tests"]
    assert result.metadata["executed_command"] == f"{sys.executable} -m pytest -q"


def test_run_tests_repo_missing_returns_failure(tmp_path: Path) -> None:
    assert run_tests(tmp_path / "missing").success is False


def test_run_tests_repo_is_file_returns_failure(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.txt"
    file_path.write_text("x\n", encoding="utf-8")

    assert run_tests(file_path).success is False


def test_run_tests_empty_command_returns_failure(tmp_path: Path) -> None:
    assert run_tests(tmp_path, command="").success is False


def test_run_tests_executable_not_found_returns_failure(tmp_path: Path) -> None:
    result = run_tests(tmp_path, command="command-that-does-not-exist")

    assert result.success is False
    assert "executable not found" in result.error


def test_run_tests_timeout_returns_timed_out(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sleep.py").write_text(
        "import time\n\n\ndef test_sleep():\n    time.sleep(2)\n",
        encoding="utf-8",
    )

    result = run_tests(tmp_path, command="pytest -q", timeout=1)

    assert result.metadata["timed_out"] is True
    assert result.metadata["status"] == "timed_out"


def test_run_tests_does_not_execute_shell_operators(tmp_path: Path) -> None:
    marker = tmp_path / "SHOULD_NOT_EXIST"

    result = run_tests(tmp_path, command=f'python -c "import sys; sys.exit(0)" && touch {marker.name}')

    assert result.metadata["returncode"] == 0
    assert marker.exists() is False


def test_run_tests_output_is_summarized_not_full_log(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_big.py").write_text(
        "def test_big():\n    assert False, '" + ("x" * 5000) + "'\n",
        encoding="utf-8",
    )

    result = run_tests(tmp_path, command="pytest -q", max_output_chars=500, max_summary_chars=200)

    assert result.success is False
    assert result.metadata["output_truncated"] is True or result.metadata["relevant_output_truncated"] is True


def test_run_tests_metadata_contains_required_fields(tmp_path: Path) -> None:
    result = run_tests(_write_pytest_repo(tmp_path, passing=True), command="pytest -q")

    for key in ("command", "original_command", "executed_command", "executed_argv", "cwd", "returncode", "status", "duration_ms", "risk"):
        assert key in result.metadata
    assert result.metadata["risk"] == ToolRisk.LOCAL_EXECUTION.value


def test_normalize_test_command_transforms_pytest_invocations() -> None:
    assert normalize_test_command("pytest") == [sys.executable, "-m", "pytest"]
    assert normalize_test_command("pytest -q") == [sys.executable, "-m", "pytest", "-q"]
    assert normalize_test_command("pytest tests/") == [sys.executable, "-m", "pytest", "tests/"]
    assert normalize_test_command("python -m pytest tests/") == [sys.executable, "-m", "pytest", "tests/"]
    assert normalize_test_command("python3 -m pytest tests/") == [sys.executable, "-m", "pytest", "tests/"]
    assert normalize_test_command(f"{sys.executable} -m pytest tests/") == [sys.executable, "-m", "pytest", "tests/"]
    assert normalize_test_command("ruff check .") == ["ruff", "check", "."]


def test_looks_like_pytest_command_recognizes_common_forms() -> None:
    from codepilot.tools.test_tools import looks_like_pytest_command

    assert looks_like_pytest_command("pytest")
    assert looks_like_pytest_command("pytest -q")
    assert looks_like_pytest_command("pytest tests/")
    assert looks_like_pytest_command("python -m pytest")
    assert looks_like_pytest_command("python3 -m pytest tests/")
    assert looks_like_pytest_command(f"{sys.executable} -m pytest tests/")
    assert not looks_like_pytest_command("echo pytest")
    assert not looks_like_pytest_command("cat pytest.ini")
    assert not looks_like_pytest_command("grep pytest README.md")
    assert not looks_like_pytest_command("python script.py pytest")
    assert not looks_like_pytest_command("ruff check .")


def test_run_tests_does_not_create_pycache(tmp_path: Path) -> None:
    repo = _write_pytest_repo(tmp_path, passing=True)

    result = run_tests(repo, command="python -m pytest -q")

    assert result.success is True
    assert not (repo / "src" / "__pycache__").exists()
    assert not (repo / "tests" / "__pycache__").exists()
