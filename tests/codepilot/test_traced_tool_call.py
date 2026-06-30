import json
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
