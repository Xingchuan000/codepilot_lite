from pathlib import Path

import pytest

from codepilot.report.trace_reader import read_trace_events


def test_read_trace_events_returns_events(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"run_id":"run-1","step":1,"event_type":"run_start"}\n', encoding="utf-8")

    events, warnings = read_trace_events(trace_path)

    assert len(events) == 1
    assert events[0]["event_type"] == "run_start"
    assert warnings == []


def test_read_trace_events_skips_blank_lines(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('\n{"run_id":"run-1","step":1,"event_type":"run_start"}\n\n', encoding="utf-8")

    events, warnings = read_trace_events(trace_path)

    assert len(events) == 1
    assert warnings == []


def test_read_trace_events_warns_on_bad_json(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"run_id":"run-1","step":1,"event_type":"run_start"}\n{bad json}\n', encoding="utf-8")

    events, warnings = read_trace_events(trace_path)

    assert len(events) == 1
    assert warnings and warnings[0].startswith("Line 2: invalid JSON:")


def test_read_trace_events_warns_on_array_line(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('["not", "an", "object"]\n', encoding="utf-8")

    events, warnings = read_trace_events(trace_path)

    assert events == []
    assert warnings == ["Line 1: expected JSON object, got list"]


def test_read_trace_events_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Trace file does not exist:"):
        read_trace_events(tmp_path / "missing.jsonl")
