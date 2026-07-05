import json
from pathlib import Path

import pytest

from codepilot.report.generator import ReportExistsError, generate_report


def _write_trace(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_generate_report_writes_markdown_and_json(tmp_path: Path) -> None:
    trace_path = tmp_path / "runs" / "run-test" / "trace.jsonl"
    _write_trace(
        trace_path,
        [
            '{"run_id":"run-test","step":1,"event_type":"run_start","metadata":{"task":"demo","repo":"%s","max_steps":3}}' % tmp_path,
            '{"run_id":"run-test","step":2,"event_type":"agent_finish","success":true,"output_summary":"done","metadata":{"status":"success","changed_files":["src/calc.py"]}}',
            '{"run_id":"run-test","step":3,"event_type":"run_end","success":true,"output_summary":"done"}',
        ],
    )

    output_path, report = generate_report(trace_path, write_json=True, overwrite=True)

    assert output_path.exists()
    assert "Run ID: run-test" in output_path.read_text(encoding="utf-8")
    assert "Status: success" in output_path.read_text(encoding="utf-8")
    assert "src/calc.py" in output_path.read_text(encoding="utf-8")
    assert report.run_id == "run-test"
    assert json.loads(output_path.with_suffix(".json").read_text(encoding="utf-8"))["run_id"] == "run-test"


def test_generate_report_respects_overwrite_flag(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(trace_path, ['{"run_id":"run-test","step":1,"event_type":"run_end","success":true}'])
    output_path = trace_path.parent / "report.md"
    output_path.write_text("exists", encoding="utf-8")

    with pytest.raises(ReportExistsError):
        generate_report(trace_path)


def test_generate_report_overwrites_existing_report(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(trace_path, ['{"run_id":"run-test","step":1,"event_type":"run_end","success":true,"output_summary":"done"}'])
    output_path = trace_path.parent / "report.md"
    output_path.write_text("exists", encoding="utf-8")

    written_path, report = generate_report(trace_path, overwrite=True)

    assert written_path == output_path
    assert report.status == "success"
