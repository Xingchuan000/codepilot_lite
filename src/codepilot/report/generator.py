from __future__ import annotations

from pathlib import Path

from codepilot.report.extractor import build_run_report
from codepilot.report.markdown import render_markdown_report
from codepilot.report.models import RunReport
from codepilot.report.trace_reader import read_trace_events


class ReportExistsError(FileExistsError):
    pass


def generate_report(
    trace_path: str | Path,
    output_path: str | Path | None = None,
    *,
    write_json: bool = False,
    json_output_path: str | Path | None = None,
    overwrite: bool = False,
) -> tuple[Path, RunReport]:
    """从 trace.jsonl 生成 Markdown 报告。"""

    trace_path = Path(trace_path)
    output_path = Path(output_path) if output_path is not None else trace_path.parent / "report.md"
    if output_path.exists() and not overwrite:
        raise ReportExistsError(f"Report already exists: {output_path}")

    events, warnings = read_trace_events(trace_path)
    report = build_run_report(events, trace_path=trace_path, warnings=warnings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown_report(report), encoding="utf-8")

    if write_json:
        json_path = Path(json_output_path) if json_output_path is not None else output_path.with_suffix(".json")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    return output_path, report
