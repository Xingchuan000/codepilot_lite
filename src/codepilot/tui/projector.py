from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from codepilot.report.extractor import build_run_report
from codepilot.report.models import RunReport
from codepilot.tui import DASHBOARD_SCHEMA_VERSION
from codepilot.tui.indexer import build_run_entry, load_report_json, load_trace_events
from codepilot.tui.artifacts import read_manifest_artifacts
from codepilot.tui.models import RunDashboardModel, TimelineRow
from codepilot.tui.redaction import redact_value, truncate_text

# 这个模块负责把索引层的结果继续投影成“展示层”模型：
# - timeline 行
# - policy / tool / MCP / test / diff 汇总
# - 只读的 dashboard 详情模型


def load_dashboard_sources(run_dir: str | Path) -> tuple[list[dict[str, Any]], RunReport | None, list[str], dict[str, str]]:
    report, report_warnings = load_report_json(run_dir)
    events, trace_warnings = load_trace_events(run_dir)
    warnings = report_warnings + trace_warnings
    provenance = {"report": "report_json" if report is not None else "unknown", "trace": "trace.jsonl" if events else "missing_trace"}
    if report is None and events:
        report = build_run_report(events, trace_path=Path(run_dir) / "trace.jsonl", warnings=trace_warnings)
        provenance["report"] = "trace_extraction"
    return events, report, warnings, provenance


def event_category(event: dict[str, Any]) -> str:
    event_type = event.get("event_type")
    tool_name = event.get("tool_name")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    if (isinstance(tool_name, str) and tool_name.startswith("mcp.")) or metadata.get("mcp") is True:
        return "mcp"
    if event_type in {"run_start", "run_end", "agent_finish"}:
        return "lifecycle"
    if event_type in {"llm_call", "agent_action", "agent_observation"}:
        return "model"
    if event_type == "policy_decision":
        return "policy"
    if event_type == "tool_call":
        return "tool"
    if event_type == "artifact_event":
        return "artifact"
    if event_type == "workflow_event":
        return "workflow"
    if event_type == "remote_event":
        return "remote"
    return "event"


def safe_mcp_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    descriptor = metadata.get("descriptor_hash")
    return {
        "source": "mcp",
        "mcp": True,
        "server_name": metadata.get("server_name"),
        "mcp_tool_name": metadata.get("mcp_tool_name"),
        "codepilot_tool_name": metadata.get("codepilot_tool_name"),
        "descriptor_hash_short": str(descriptor)[:12] if descriptor else None,
        "exposed_to_agent": metadata.get("exposed_to_agent"),
        "trust_level": metadata.get("trust_level"),
        "transport": metadata.get("transport"),
    }


def event_to_timeline_row(event: dict[str, Any], *, max_text_chars: int = 500) -> TimelineRow:
    event_type = str(event.get("event_type") or "unknown")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    category = event_category(event)
    title_map = {
        "run_start": "Run started",
        "llm_call": "LLM call",
        "agent_action": "Agent action",
        "policy_decision": "Policy decision",
        "tool_call": "Tool call",
        "agent_observation": "Agent observation",
        "agent_finish": "Agent finish",
        "run_end": "Run ended",
    }
    title = title_map.get(event_type, f"Event: {event_type}")
    # MCP 事件只保留安全摘要，避免把结构化内容和环境信息直接展示出来。
    if category == "mcp":
        metadata_out = safe_mcp_metadata(metadata)
    elif event_type == "llm_call":
        metadata_out = {
            "response_chars": len(str(event.get("output_preview") or event.get("output_summary") or "")),
            "message_count": metadata.get("message_count"),
            "model": metadata.get("model"),
        }
    elif event_type == "agent_action":
        metadata_out = {
            "action_type": metadata.get("action_type"),
            "parse_success": metadata.get("parse_success"),
            "normalization_applied": metadata.get("normalization_applied"),
        }
    else:
        metadata_out = redact_value({key: value for key, value in metadata.items() if key != "input"}, max_string_chars=max_text_chars)
        if not isinstance(metadata_out, dict):
            metadata_out = {}
    status = metadata.get("status") if isinstance(metadata.get("status"), str) else None
    if status is None:
        if event.get("success") is True:
            status = "success"
        elif event.get("success") is False:
            status = "failed"
    executed = metadata.get("executed") if isinstance(metadata.get("executed"), bool) else None
    policy_decision = event.get("policy_decision") or metadata.get("policy_decision")
    if event_type == "tool_call":
        executed = True
    elif policy_decision == "deny":
        executed = False
    elif policy_decision == "ask" and metadata.get("approved") is False:
        executed = False
    summary = event.get("output_summary") or event.get("error")
    if isinstance(summary, str):
        summary = truncate_text(str(redact_value(summary, max_string_chars=max_text_chars)), max_chars=max_text_chars)
    else:
        summary = None
    risk = event.get("risk") or metadata.get("risk")
    return TimelineRow(
        step=event.get("step") if isinstance(event.get("step"), int) else None,
        event_type=event_type,
        title=title,
        status=status,
        category=category,
        tool_name=event.get("tool_name") if isinstance(event.get("tool_name"), str) else None,
        policy_decision=str(policy_decision) if policy_decision is not None else None,
        executed=executed,
        risk=str(risk) if risk is not None else None,
        output_summary=summary,
        metadata=metadata_out,
    )


def build_policy_summary(events: list[dict[str, Any]]) -> dict[str, int]:
    summary = Counter({"total": 0, "allow": 0, "ask": 0, "deny": 0, "approved": 0, "unapproved": 0, "executed": 0, "unexecuted": 0})
    for event in events:
        if event.get("event_type") != "policy_decision":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        decision = event.get("policy_decision") or metadata.get("policy_decision")
        summary["total"] += 1
        if decision in {"allow", "ask", "deny"}:
            summary[str(decision)] += 1
        if metadata.get("approved") is True:
            summary["approved"] += 1
        elif metadata.get("approved") is False:
            summary["unapproved"] += 1
        if metadata.get("executed") is True:
            summary["executed"] += 1
        if decision == "deny" or (decision == "ask" and metadata.get("approved") is False):
            summary["unexecuted"] += 1
    return dict(summary)


def build_tool_summary(events: list[dict[str, Any]]) -> dict[str, int]:
    summary: Counter[str] = Counter()
    for event in events:
        if event.get("event_type") != "tool_call":
            continue
        tool_name = str(event.get("tool_name") or "unknown_tool")
        summary[tool_name] += 1
    return dict(summary)


def build_mcp_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    servers: set[str] = set()
    descriptor_hashes: set[str] = set()
    total_tool_calls = 0
    exposed_to_agent_count = 0
    denied_count = 0
    for event in events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        tool_name = event.get("tool_name")
        is_mcp = (isinstance(tool_name, str) and tool_name.startswith("mcp.")) or metadata.get("mcp") is True
        if not is_mcp:
            continue
        if event.get("event_type") == "tool_call":
            total_tool_calls += 1
        server = metadata.get("server_name")
        if isinstance(server, str) and server:
            servers.add(server)
        descriptor = metadata.get("descriptor_hash")
        if isinstance(descriptor, str) and descriptor:
            descriptor_hashes.add(descriptor[:12])
        if metadata.get("exposed_to_agent") is True:
            exposed_to_agent_count += 1
        if event.get("event_type") == "policy_decision" and (event.get("policy_decision") or metadata.get("policy_decision")) == "deny":
            denied_count += 1
    return {
        "total_tool_calls": total_tool_calls,
        "servers": sorted(servers),
        "exposed_to_agent_count": exposed_to_agent_count,
        "denied_count": denied_count,
        "descriptor_hashes": sorted(descriptor_hashes),
    }


def build_test_summary(events: list[dict[str, Any]], report: RunReport | None) -> dict[str, Any]:
    if report is not None and (report.tests.status is not None or report.tests.summary is not None):
        return {
            "status": report.tests.status,
            "summary": report.tests.summary,
            "command": redact_value(report.tests.command),
            "returncode": report.tests.returncode,
            "timed_out": report.tests.timed_out,
            "failed_tests": redact_value(report.tests.failed_tests),
        }
    for event in reversed(events):
        if event.get("tool_name") != "run_tests":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        return {
            "status": metadata.get("status"),
            "summary": event.get("output_summary") or metadata.get("summary_line"),
            "command": redact_value(metadata.get("command")),
            "returncode": metadata.get("returncode"),
            "timed_out": metadata.get("timed_out"),
            "failed_tests": redact_value(metadata.get("failed_tests") or []),
        }
    return {}


def build_diff_summary(events: list[dict[str, Any]], report: RunReport | None) -> dict[str, Any]:
    if report is not None and report.diff.checked:
        return {
            "checked": report.diff.checked,
            "paths": redact_value(report.diff.paths),
            "summary": report.diff.summary,
            "truncated": report.diff.truncated,
        }
    for event in reversed(events):
        if event.get("tool_name") != "git_diff":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        paths = metadata.get("paths") if isinstance(metadata.get("paths"), list) else []
        return {
            "checked": True,
            "paths": redact_value([item for item in paths if isinstance(item, str)]),
            "summary": event.get("output_summary"),
            "truncated": metadata.get("truncated") is True,
        }
    return {"checked": False, "paths": [], "summary": None, "truncated": False}


def build_workflow_summary(entry, report: RunReport | None, manifest_summary: dict[str, Any]) -> dict[str, Any]:
    safety = manifest_summary.get("safety_summary") if isinstance(manifest_summary.get("safety_summary"), dict) else {}
    return {
        "run_type": entry.run_type,
        "source_workflow": entry.source_workflow,
        "has_issue_artifacts": entry.has_issue_artifacts,
        "has_pr_artifacts": entry.has_pr_artifacts,
        "artifact_count": len(entry.artifacts),
        "manifest_status": manifest_summary.get("status"),
        "manifest_success": manifest_summary.get("success"),
        "safety_decision": manifest_summary.get("safety_decision"),
        "baseline_dirty": safety.get("baseline_dirty"),
        "used_worktree": manifest_summary.get("used_worktree"),
        "contains_preexisting_changes": safety.get("contains_preexisting_changes"),
    }


def build_dashboard_model(
    run_dir: str | Path,
    *,
    max_timeline_rows: int = 200,
    max_text_chars: int = 500,
) -> RunDashboardModel:
    entry = build_run_entry(run_dir)
    events, report, source_warnings, provenance = load_dashboard_sources(run_dir)
    timeline = tuple(event_to_timeline_row(event, max_text_chars=max_text_chars) for event in events)
    warnings = list(entry.warnings) + source_warnings
    # timeline 太长时只截断展示层，不回写任何原始数据。
    if len(timeline) > max_timeline_rows:
        warnings.append("timeline_truncated")
        timeline = timeline[:max_timeline_rows]
    model = RunDashboardModel(
        schema_version=DASHBOARD_SCHEMA_VERSION,
        entry=entry,
        timeline=timeline,
        policy_summary=build_policy_summary(events),
        tool_summary=build_tool_summary(events),
        mcp_summary=build_mcp_summary(events),
        test_summary=build_test_summary(events, report),
        diff_summary=build_diff_summary(events, report),
        workflow_summary=build_workflow_summary(entry, report, read_manifest_artifacts(Path(run_dir))[2]),
        artifact_summary=entry.artifacts,
        source_provenance={**entry.source_provenance, **provenance},
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return model
