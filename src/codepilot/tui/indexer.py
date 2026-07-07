from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.report.extractor import build_run_report
from codepilot.report.models import RunReport
from codepilot.report.trace_reader import read_trace_events
from codepilot.tui.artifacts import merge_artifact_refs, read_manifest_artifacts, scan_filesystem_artifacts
from codepilot.tui.models import RunArtifactRef, RunIndexEntry
from codepilot.tui.redaction import redact_value, truncate_text

# 这个模块只负责“索引层”：
# 1. 找出 run 目录
# 2. 读取 trace / report / manifest
# 3. 归纳成适合展示的 RunIndexEntry


def list_run_dirs(runs_dir: str | Path) -> list[Path]:
    root = Path(runs_dir)
    if not root.exists():
        raise FileNotFoundError(f"runs_dir does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"runs_dir is not a directory: {root}")
    return sorted(
        [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "__MACOSX"],
        key=lambda p: p.name,
    )


def load_report_json(run_dir: str | Path) -> tuple[RunReport | None, list[str]]:
    path = Path(run_dir) / "report.json"
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, ["bad_report_json"]
    try:
        return RunReport.model_validate(payload), []
    except Exception:
        return None, ["bad_report_json_schema"]


def load_trace_events(run_dir: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = Path(run_dir) / "trace.jsonl"
    if not path.exists():
        return [], ["missing_trace"]
    try:
        events, warnings = read_trace_events(path)
    except Exception as exc:
        return [], [f"bad_trace_json: {exc}"]
    return events, [f"bad_trace_json: {warning}" for warning in warnings]


def _dedupe(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def first_timestamp(events: list[dict[str, Any]], event_type: str) -> str | None:
    for event in events:
        if event.get("event_type") == event_type and isinstance(event.get("timestamp"), str):
            return event["timestamp"]
    return None


def last_timestamp(events: list[dict[str, Any]], event_type: str) -> str | None:
    for event in reversed(events):
        if event.get("event_type") == event_type and isinstance(event.get("timestamp"), str):
            return event["timestamp"]
    return None


def updated_at_from_artifacts(run_dir: Path, artifacts: tuple[RunArtifactRef, ...]) -> str | None:
    mtimes = [item.path.stat().st_mtime for item in artifacts if item.exists and item.path.exists()]
    if not mtimes:
        try:
            return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=UTC).isoformat()
        except FileNotFoundError:
            return None
    return datetime.fromtimestamp(max(mtimes), tz=UTC).isoformat()


def extract_task(events: list[dict[str, Any]], report: RunReport | None) -> str:
    for event in events:
        if event.get("event_type") != "run_start":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        task = metadata.get("task")
        if isinstance(task, str) and task.strip():
            return truncate_text(str(redact_value(task, max_string_chars=160)), max_chars=160)
    if report is not None and report.task:
        return truncate_text(str(redact_value(report.task, max_string_chars=160)), max_chars=160)
    return ""


def extract_changed_files(
    events: list[dict[str, Any]],
    report: RunReport | None,
    manifest_summary: dict[str, Any],
) -> tuple[str, ...]:
    changed: list[str] = []
    if report is not None:
        changed.extend(report.changed_files)
    patch = manifest_summary.get("patch") if isinstance(manifest_summary.get("patch"), dict) else {}
    if isinstance(patch, dict):
        changed.extend(str(item) for item in patch.get("changed_files", []) if isinstance(item, str))
    for event in events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        for key in ("changed_files", "touched_paths"):
            value = metadata.get(key)
            if isinstance(value, list):
                changed.extend(str(item) for item in value if isinstance(item, str))
        if event.get("tool_name") in {"git_status", "apply_patch", "replace_range", "git_diff"}:
            path_value = metadata.get("path")
            if isinstance(path_value, str):
                changed.append(path_value)
    return tuple(sorted(dict.fromkeys(item for item in changed if item)))


def extract_test_status(events: list[dict[str, Any]], report: RunReport | None) -> str | None:
    if report is not None and report.tests.status:
        return report.tests.status
    for event in reversed(events):
        if event.get("tool_name") != "run_tests":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        status = metadata.get("status")
        if isinstance(status, str):
            return status
    return None


def count_policy(events: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": 0, "allow": 0, "ask": 0, "deny": 0, "approved": 0, "unapproved": 0, "executed": 0, "unexecuted": 0}
    for event in events:
        if event.get("event_type") != "policy_decision":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        decision = event.get("policy_decision") or metadata.get("policy_decision")
        approved = metadata.get("approved")
        executed = metadata.get("executed")
        summary["total"] += 1
        if decision in summary:
            summary[str(decision)] += 1
        if approved is True:
            summary["approved"] += 1
        elif approved is False:
            summary["unapproved"] += 1
        if executed is True:
            summary["executed"] += 1
        if decision == "deny" or (decision == "ask" and approved is False):
            summary["unexecuted"] += 1
    return summary


def count_tools(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event.get("event_type") == "tool_call")


def has_mcp_events(events: list[dict[str, Any]]) -> bool:
    for event in events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        tool_name = event.get("tool_name")
        if (isinstance(tool_name, str) and tool_name.startswith("mcp.")) or metadata.get("mcp") is True:
            return True
    return False


def derive_status(
    *,
    events: list[dict[str, Any]],
    report: RunReport | None,
    manifest_summary: dict[str, Any],
    run_dir: Path,
) -> tuple[str, list[str], str]:
    warnings: list[str] = []
    trace_status: str | None = None
    provenance = "unknown"
    for event in reversed(events):
        if event.get("event_type") not in {"run_end", "agent_finish"}:
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        status = metadata.get("status")
        if isinstance(status, str) and status:
            trace_status = status
        elif event.get("success") is True:
            trace_status = "success"
        elif event.get("success") is False:
            output_summary = str(event.get("output_summary") or "")
            trace_status = output_summary if output_summary in {"max_steps_exceeded", "llm_exhausted", "llm_error"} else "failed"
        if trace_status is None:
            output_summary = str(event.get("output_summary") or "")
            trace_status = output_summary if output_summary in {"max_steps_exceeded", "llm_exhausted", "llm_error"} else "unknown"
        provenance = f"trace.{event.get('event_type')}"
        break

    report_status = report.status if report is not None else None
    manifest_status = manifest_summary.get("status") if isinstance(manifest_summary.get("status"), str) else None

    # trace 的最终状态优先级最高，因为它最接近真实执行结果。
    if trace_status is not None:
        if report_status is not None and report_status != trace_status:
            warnings.append("status_conflict_trace_report")
        if manifest_status is not None and manifest_status != trace_status:
            warnings.append("status_conflict_report_manifest")
        return trace_status, warnings, provenance

    # trace 不可用时，再按 report / manifest 回退。
    if report_status is not None:
        if manifest_status is not None and manifest_status != report_status:
            warnings.append("status_conflict_report_manifest")
        return report_status, warnings, "report_json"

    if manifest_status is not None:
        return manifest_status, warnings, "artifact_manifest"

    # 目录很新但没有结束事件，视为仍在运行。
    if events and (datetime.now(UTC) - datetime.fromtimestamp(run_dir.stat().st_mtime, tz=UTC)).total_seconds() < 3600:
        return "running", warnings, "filesystem_mtime"

    return "unknown", warnings, "unknown"


def infer_run_type(
    run_id: str,
    artifacts: tuple[RunArtifactRef, ...],
    events: list[dict[str, Any]],
    manifest_summary: dict[str, Any],
) -> str:
    kinds = {item.kind for item in artifacts}
    # 先看明确的产物类型，再看事件和 run_id 前缀。
    if "post_pr_manifest" in kinds or "post_pr_report" in kinds:
        return "post_pr"
    if "pr_feedback_manifest" in kinds:
        return "pr_feedback"
    if "auto_pr_manifest" in kinds:
        return "auto_pr"
    if kinds & {"pr_assist_manifest", "pr_body", "manual_pr_commands", "review_checklist"}:
        return "pr_assist"
    if "issue_json" in kinds:
        return "issue_workflow"
    if has_mcp_events(events):
        return "mcp_demo"
    if run_id.startswith("issue-"):
        return "issue_workflow"
    if run_id.startswith("mcp-"):
        return "mcp_demo"
    if run_id.startswith("run-") or run_id.startswith("demo-"):
        return "agent_run"
    return "unknown"


def build_run_entry(run_dir: str | Path) -> RunIndexEntry:
    run_dir_path = Path(run_dir)
    if not run_dir_path.exists():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir_path}")
    run_id = run_dir_path.name

    manifest_refs, manifest_warnings, manifest_summary = read_manifest_artifacts(run_dir_path)
    fs_refs = scan_filesystem_artifacts(run_dir_path)
    artifacts = merge_artifact_refs(manifest_refs, fs_refs)
    report, report_warnings = load_report_json(run_dir_path)
    report_source = "report_json" if report is not None else None
    events, trace_warnings = load_trace_events(run_dir_path)
    trace_path = run_dir_path / "trace.jsonl"
    if report is None and events:
        report = build_run_report(events, trace_path=trace_path, warnings=trace_warnings)
        report_source = "trace_extraction"

    status, status_warnings, status_provenance = derive_status(events=events, report=report, manifest_summary=manifest_summary, run_dir=run_dir_path)
    task = extract_task(events, report)
    changed_files = extract_changed_files(events, report, manifest_summary)
    test_status = extract_test_status(events, report)
    entry_warnings = _dedupe(
        manifest_warnings
        + report_warnings
        + trace_warnings
        + status_warnings
        + ([] if trace_path.exists() else ["missing_trace"])
    )
    updated_at = updated_at_from_artifacts(run_dir_path, artifacts)
    if updated_at is None and trace_path.exists():
        updated_at = first_timestamp(events, "run_end") or last_timestamp(events, "agent_finish")
    if updated_at is None:
        try:
            updated_at = datetime.fromtimestamp(run_dir_path.stat().st_mtime, tz=UTC).isoformat()
        except FileNotFoundError:
            updated_at = None

    summary_source = report_source or ("trace_extraction" if events else "unknown")
    source_provenance = {
        "summary": summary_source,
        "status": status_provenance,
        "task": "report_json" if report_source == "report_json" and report and report.task else ("trace.run_start" if task else "unknown"),
        "tests": report_source if report is not None and report.tests.status is not None else ("trace.run_tests" if any(event.get("tool_name") == "run_tests" for event in events) else "unknown"),
        "diff": report_source if report is not None and report.diff.checked else ("trace.git_diff" if any(event.get("tool_name") == "git_diff" for event in events) else "unknown"),
        "artifacts": "artifact_manifest" if manifest_refs else "filesystem",
    }
    has_issue_artifacts = any(item.kind == "issue_json" for item in artifacts)
    has_pr_artifacts = any(item.kind in {"pr_summary", "pr_body", "manual_pr_commands", "review_checklist"} for item in artifacts)
    return RunIndexEntry(
        run_id=run_id,
        run_dir=run_dir_path,
        run_type=infer_run_type(run_id, artifacts, events, manifest_summary),
        source_workflow=str(manifest_summary.get("source_workflow") or manifest_summary.get("generator") or "unknown"),
        status=status,
        task=task,
        started_at=first_timestamp(events, "run_start"),
        ended_at=last_timestamp(events, "run_end") or last_timestamp(events, "agent_finish"),
        updated_at=updated_at,
        tool_call_count=count_tools(events),
        policy_decision_count=count_policy(events)["total"],
        policy_denied_count=count_policy(events)["deny"],
        approval_required_count=sum(
            1
            for event in events
            if event.get("event_type") == "policy_decision"
            and (
                (event.get("metadata") if isinstance(event.get("metadata"), dict) else {}).get("requires_approval") is True
                or (event.get("policy_decision") or (event.get("metadata") if isinstance(event.get("metadata"), dict) else {}).get("policy_decision")) == "ask"
            )
        ),
        unexecuted_action_count=count_policy(events)["unexecuted"],
        test_status=test_status,
        changed_files=changed_files,
        has_mcp=has_mcp_events(events),
        has_issue_artifacts=has_issue_artifacts,
        has_pr_artifacts=has_pr_artifacts,
        artifacts=artifacts,
        source_provenance=source_provenance,
        warnings=entry_warnings,
    )


def build_run_index(
    runs_dir: str | Path,
    *,
    limit: int | None = None,
    status: str | None = None,
    run_type: str | None = None,
) -> list[RunIndexEntry]:
    if limit is not None and limit <= 0:
        return []
    runs = list_run_dirs(runs_dir)
    entries: list[RunIndexEntry] = []
    for run_dir in runs:
        try:
            entry = build_run_entry(run_dir)
        except Exception as exc:
            entry = RunIndexEntry(run_id=run_dir.name, run_dir=run_dir, status="unknown", warnings=(f"run_index_error: {exc}",))
        if status is not None and entry.status != status:
            continue
        if run_type is not None and entry.run_type != run_type:
            continue
        entries.append(entry)

    def _sort_key(entry: RunIndexEntry) -> tuple[float, str]:
        if entry.updated_at:
            try:
                return datetime.fromisoformat(entry.updated_at).timestamp(), entry.run_id
            except ValueError:
                pass
        try:
            return entry.run_dir.stat().st_mtime, entry.run_id
        except FileNotFoundError:
            return 0.0, entry.run_id

    entries.sort(key=_sort_key, reverse=True)
    if limit is not None:
        return entries[:limit]
    return entries
