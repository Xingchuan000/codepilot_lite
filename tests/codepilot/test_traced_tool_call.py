import json
import subprocess
from pathlib import Path

from codepilot.tools.registry import call_tool, call_tool_traced
from codepilot.trace.logger import TraceLogger


def test_call_tool_does_not_write_trace(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")

    result = call_tool("list_files", repo=tmp_path, path=".", max_depth=1)

    assert result.success is True
    assert not (tmp_path / "runs").exists()


def test_call_tool_traced_writes_event(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    result = call_tool_traced(
        "list_files",
        trace_logger=logger,
        repo=tmp_path,
        path=".",
        max_depth=1,
    )

    assert result.success is True
    lines = logger.trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["tool_name"] == "list_files"
    assert data["risk"] == "read_only"
    assert data["side_effect"] == "none"
    assert data["default_permission"] == "allow"
    assert data["metadata"]["output_chars"] == len(result.output)


def test_call_tool_traced_redacts_sensitive_input_and_serializes_paths(tmp_path: Path) -> None:
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced(
        "unknown",
        trace_logger=logger,
        repo=tmp_path,
        path=Path("."),
        token="abc123",
        password="pw",
        secret="s3cr3t",
        api_key="key",
        authorization="Bearer xxx",
    )

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert data["input"]["path"] == "."
    assert data["input"]["token"] == "[REDACTED]"
    assert data["input"]["password"] == "[REDACTED]"
    assert data["input"]["secret"] == "[REDACTED]"
    assert data["input"]["api_key"] == "[REDACTED]"
    assert data["input"]["authorization"] == "[REDACTED]"


def test_call_tool_traced_unknown_tool_still_writes_trace(tmp_path: Path) -> None:
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    result = call_tool_traced("unknown", trace_logger=logger, repo=tmp_path)

    assert result.success is False
    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert data["tool_name"] == "unknown"
    assert data["risk"] is None
    assert data["side_effect"] is None
    assert data["default_permission"] is None


def test_call_tool_traced_marks_truncated_preview(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced("list_files", trace_logger=logger, output_preview_chars=1, repo=tmp_path, path=".", max_depth=1)

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert data["metadata"]["output_preview_truncated"] is True


def test_call_tool_traced_truncates_long_string_input(tmp_path: Path) -> None:
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced("unknown", trace_logger=logger, patch="x" * 5000)

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert data["input"]["patch"].endswith("... truncated")
    assert len(data["input"]["patch"]) == 4000


def test_call_tool_traced_apply_patch_dry_run_records_local_write_metadata(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("old\n", encoding="utf-8")
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced(
        "apply_patch",
        trace_logger=logger,
        repo=tmp_path,
        patch="diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        dry_run=True,
    )

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert data["tool_name"] == "apply_patch"
    assert data["risk"] == "local_write"
    assert data["side_effect"] == "local_write"
    assert data["default_permission"] == "ask"
    assert data["metadata"]["touched_paths"] == ["src/a.py"]


def test_call_tool_traced_run_tests_writes_test_metadata(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced("run_tests", trace_logger=logger, repo=tmp_path, command="python -m pytest -q")

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert data["tool_name"] == "run_tests"
    assert data["risk"] == "local_execution"
    assert data["side_effect"] == "local_exec"
    assert "status" in data["metadata"]
    assert "returncode" in data["metadata"]
    assert "failed_tests" in data["metadata"]


def test_call_tool_traced_git_status_writes_changed_files_metadata(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=tmp_path, check=True)
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced("git_status", trace_logger=logger, repo=tmp_path)

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert "changed_files" in data["metadata"]
    assert "clean" in data["metadata"]


def test_call_tool_traced_git_diff_writes_diff_metadata(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/a.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src" / "a.py").write_text("new\n", encoding="utf-8")
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced("git_diff", trace_logger=logger, repo=tmp_path, path="src/a.py", include_content=True)

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert "include_content" in data["metadata"]
    assert "path" in data["metadata"]
    assert "staged" in data["metadata"]
    assert "truncated" in data["metadata"]


def test_call_tool_traced_run_tests_output_preview_is_summary(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_big.py").write_text(
        "def test_big():\n    assert False, '" + ("x" * 3000) + "'\n",
        encoding="utf-8",
    )
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-test")

    call_tool_traced(
        "run_tests",
        trace_logger=logger,
        repo=tmp_path,
        command="python -m pytest -q",
        max_output_chars=500,
        max_summary_chars=200,
        output_preview_chars=100,
    )

    data = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])

    assert len(data["output_preview"]) <= 100
