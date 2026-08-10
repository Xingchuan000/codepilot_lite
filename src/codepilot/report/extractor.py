from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from codepilot.report.models import PolicyViolationReport, RunReport, ToolStepReport

_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "password", "secret", "token")
_SENSITIVE_PATH_PARTS = (".env", "secrets", ".ssh")
_MAX_PREVIEW_CHARS = 4000


def _get(event: dict[str, Any], key: str, default: Any = None) -> Any:
    return event.get(key, default)


def _get_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None:
        return None
    return str(value)


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_as_str(value)] if _as_str(value) is not None else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            text = _as_str(item)
            if text is not None:
                items.append(text)
        return items
    text = _as_str(value)
    return [text] if text is not None else []


def _ordered_add(items: list[str], value: str | None) -> None:
    if value is not None and value not in items:
        items.append(value)


def _ordered_extend(items: list[str], values: list[str]) -> None:
    for value in values:
        _ordered_add(items, value)


def _looks_like_secret_text(text: str) -> bool:
    if re.search(r"(?im)^\s*(api[_-]?key|authorization|password|secret|token)\s*=", text):
        return True
    return "BEGIN PRIVATE KEY" in text or "ghp_" in text or "sk-" in text


def _sanitize_for_report(value: Any, max_chars: int = _MAX_PREVIEW_CHARS) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _sanitize_for_report(item, max_chars=max_chars)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_report(item, max_chars=max_chars) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_report(item, max_chars=max_chars) for item in value]
    if isinstance(value, set):
        return sorted(str(_sanitize_for_report(item, max_chars=max_chars)) for item in value)
    if isinstance(value, Path):
        return _sanitize_for_report(str(value), max_chars=max_chars)
    if isinstance(value, str):
        if _looks_like_secret_text(value):
            return "[REDACTED]"
        if len(value) > max_chars:
            return f"{value[: max(0, max_chars - len('... truncated'))]}... truncated"
        return value
    return value


def _sanitize_dict_for_report(value: dict[str, Any], max_chars: int = _MAX_PREVIEW_CHARS) -> dict[str, Any]:
    return _sanitize_for_report(value, max_chars=max_chars)


def _normalize_path_for_report(path: str | Path | None, repo: str | Path | None = None) -> str | None:
    if path is None:
        return None
    path_text = str(path)
    if repo is None:
        return path_text
    repo_path = Path(repo)
    path_obj = Path(path_text)
    if path_obj.is_absolute():
        try:
            return str(path_obj.relative_to(repo_path))
        except ValueError:
            return path_text
    return path_text


def _event_run_id(events: list[dict[str, Any]]) -> str:
    for event in events:
        run_id = _as_str(event.get("run_id"))
        if run_id is not None:
            return run_id
    return "unknown-run"


def _policy_decision_value(event: dict[str, Any]) -> str | None:
    return _as_str(event.get("policy_decision"))


def _delivery_kind_value(event: dict[str, Any]) -> str | None:
    return _as_str(_get_metadata(event).get("delivery_kind"))


def _policy_approved_value(event: dict[str, Any]) -> bool | None:
    value = _get_metadata(event).get("approved")
    return value if isinstance(value, bool) else None


def _policy_executed_value(event: dict[str, Any]) -> bool | None:
    value = _get_metadata(event).get("executed")
    return value if isinstance(value, bool) else None


def _policy_key(event: dict[str, Any]) -> str:
    return _as_str(_get_metadata(event).get("action_id")) or _tool_key(event)


def _tool_key(event: dict[str, Any]) -> str:
    return _as_str(_get(event, "tool_name")) or "unknown-tool"


def _is_sensitive_diff_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if any(part == ".ssh" for part in parts):
        return True
    if any(part == "secrets" for part in parts):
        return True
    return normalized.endswith(".env") or "/.env." in normalized or normalized.startswith(".env.")


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _update_test_report(report: RunReport, event: dict[str, Any]) -> None:
    metadata = _get_metadata(event)
    if _tool_key(event) != "run_tests":
        return
    report.tests.status = _as_str(metadata.get("status"))
    report.tests.command = _as_str(metadata.get("command"))
    report.tests.original_command = _as_str(metadata.get("original_command"))
    report.tests.executed_command = _as_str(metadata.get("executed_command"))
    report.tests.failed_tests = _as_str_list(metadata.get("failed_tests"))
    report.tests.summary = _as_str(_get(event, "output_summary")) or _as_str(metadata.get("summary_line"))
    report.tests.returncode = _as_int(metadata.get("returncode"))
    report.tests.timed_out = _as_bool(metadata.get("timed_out"))


def _update_changed_files(report: RunReport, values: list[str]) -> None:
    for value in values:
        normalized = _normalize_path_for_report(value, report.repo)
        if normalized is not None:
            _ordered_add(report.changed_files, normalized)


def build_run_report(
    events: list[dict[str, Any]],
    *,
    trace_path: str | Path | None = None,
    warnings: list[str] | None = None,
) -> RunReport:
    """把 trace 事件压缩成可读的 RunReport。"""

    report = RunReport(
        run_id=_event_run_id(events),
        trace_path=str(trace_path) if trace_path is not None else None,
        warnings=list(warnings or []),
    )
    report.steps = max((_as_int(event.get("step")) or 0 for event in events), default=0)

    pending_policies: list[ToolStepReport] = []
    saw_run_start = False
    saw_agent_finish = False
    for event in events:
        event_type = _as_str(event.get("event_type"))
        step = _as_int(event.get("step"))
        metadata = _get_metadata(event)
        tool_name = _tool_key(event)

        if event_type not in {
            "run_start",
            "llm_call",
            "agent_action",
            "policy_decision",
            "permission_request",
            "permission_response",
            "tool_call",
            "tool_result",
            "agent_observation",
            "agent_finish",
            "run_end",
            "run_cancelled",
        }:
            _append_warning(report.warnings, f"Unknown event_type at step {step if step is not None else 'unknown'}: {event_type}")
            continue

        if event_type == "run_start":
            saw_run_start = True
            report.task = report.task or _as_str(metadata.get("task"))
            report.repo = report.repo or _as_str(metadata.get("repo"))
            report.max_steps = report.max_steps or _as_int(metadata.get("max_steps"))
            report.policy_mode = report.policy_mode or _as_str(metadata.get("policy_mode"))
            report.task_intent = report.task_intent or _as_str(metadata.get("task_intent"))
            if report.requires_evidence is None:
                report.requires_evidence = _as_bool(metadata.get("requires_evidence"))
            _ordered_extend(report.evidence_reasons, _as_str_list(metadata.get("initial_evidence_reasons")))
            continue

        if event_type == "llm_call":
            model = _as_str(metadata.get("model"))
            if report.model is None and model is not None:
                report.model = model
            continue

        if event_type == "agent_action":
            if _as_bool(metadata.get("finish_blocked_by_evidence")) is True:
                _append_warning(report.warnings, f"Step {step}: finish success blocked by evidence gate.")
            elif _as_bool(event.get("success")) is False:
                _append_warning(report.warnings, f"Step {step}: agent_action failed: {_as_str(event.get('error')) or 'unknown error'}")
            if event.get("error") and _as_bool(metadata.get("finish_blocked_by_evidence")) is not True:
                _append_warning(report.warnings, f"Step {step}: agent_action failed: {_as_str(event.get('error'))}")
            continue

        if event_type == "policy_decision":
            decision = _policy_decision_value(event)
            approved = _policy_approved_value(event)
            executed = _policy_executed_value(event)
            report.policy.total += 1
            if decision == "allow":
                report.policy.allowed += 1
            elif decision == "ask":
                report.policy.asked += 1
            elif decision == "deny":
                report.policy.denied += 1
            if approved is True:
                report.policy.approved += 1
            violation = decision == "deny" or (decision == "ask" and approved is False)
            policy_step = ToolStepReport(
                step=step,
                tool_name=tool_name,
                policy_decision=decision,
                approved=approved,
                executed=executed,
                risk_level=_as_str(metadata.get("risk")) or _as_str(event.get("risk")),
                side_effect=_as_str(metadata.get("side_effect")) or _as_str(event.get("side_effect")),
                metadata=_sanitize_dict_for_report(metadata),
            )
            if violation:
                report.policy.violations.append(
                    PolicyViolationReport(
                        step=step,
                        tool_name=tool_name,
                        decision=decision,
                        reason=_as_str(event.get("policy_reason")),
                        rule=_as_str(event.get("policy_rule")),
                        approved=approved,
                        executed=False if decision in {"deny", "ask"} and approved is not True else executed,
                    )
                )
                policy_step.executed = False if decision in {"deny", "ask"} and approved is not True else executed
                report.tool_steps.append(policy_step)
                continue
            pending_policies.append(policy_step)
            continue

        if event_type == "tool_call":
            tool_step = ToolStepReport(
                step=step,
                tool_name=tool_name,
                success=_as_bool(event.get("success")),
                executed=True,
                summary=_as_str(_get(event, "output_summary")),
                error=_as_str(_get(event, "error")),
                risk_level=_as_str(event.get("risk")),
                side_effect=_as_str(event.get("side_effect")),
                arguments_preview=_sanitize_dict_for_report(_get(event, "input") if isinstance(_get(event, "input"), dict) else {}),
                metadata=_sanitize_dict_for_report(metadata),
            )
            matched_index = None
            for index in range(len(pending_policies) - 1, -1, -1):
                if pending_policies[index].tool_name == tool_name:
                    matched_index = index
                    break
            if matched_index is not None:
                pending = pending_policies.pop(matched_index)
                pending.step = pending.step or tool_step.step
                pending.success = tool_step.success
                pending.executed = True
                pending.summary = pending.summary or tool_step.summary
                pending.error = pending.error or tool_step.error
                pending.risk_level = pending.risk_level or tool_step.risk_level
                pending.side_effect = pending.side_effect or tool_step.side_effect
                pending.arguments_preview = tool_step.arguments_preview
                pending.metadata = tool_step.metadata
                if pending.policy_decision in {"allow", "ask"} and pending.approved is True:
                    pending.executed = True
                report.tool_steps.append(pending)
            else:
                report.tool_steps.append(tool_step)

            if tool_name == "run_tests":
                _update_test_report(report, event)
            if tool_name == "git_diff":
                report.diff.checked = True
                report.diff.summary = _as_str(_get(event, "output_summary"))
                path_value = _as_str(metadata.get("path"))
                if path_value is not None:
                    _ordered_add(report.diff.paths, _normalize_path_for_report(path_value, report.repo) or path_value)
                report.diff.truncated = report.diff.truncated or any(
                    _as_bool(metadata.get(key)) is True
                    for key in ("truncated", "char_truncated", "line_truncated", "output_preview_truncated")
                )
                preview = _as_str(_get(event, "output_preview"))
                if preview is not None:
                    if path_value is not None and _is_sensitive_diff_path(path_value):
                        _append_warning(report.warnings, f"git_diff preview skipped for sensitive path: {path_value}")
                    else:
                        raw_preview_truncated = len(preview) > _MAX_PREVIEW_CHARS
                        sanitized_preview = _sanitize_for_report(preview, max_chars=_MAX_PREVIEW_CHARS)
                        if isinstance(sanitized_preview, str):
                            report.diff.preview = sanitized_preview
                            report.diff.truncated = (
                                report.diff.truncated
                                or raw_preview_truncated
                                or sanitized_preview.endswith("... truncated")
                            )
            if tool_name == "git_status":
                _update_changed_files(report, _as_str_list(metadata.get("changed_files")))
            if tool_name == "replace_range" and _as_bool(metadata.get("changed")) is True:
                _update_changed_files(report, [path for path in _as_str_list(metadata.get("path")) if path])
            if tool_name == "apply_patch":
                _update_changed_files(report, _as_str_list(metadata.get("touched_paths")))
            if tool_name == "git_diff" and _as_str(metadata.get("path")) is not None:
                _update_changed_files(report, [_as_str(metadata.get("path")) or ""])
            continue

        if event_type == "tool_result":
            continue

        if event_type == "agent_observation":
            continue

        if event_type == "agent_finish":
            saw_agent_finish = True
            report.status = report.status or _as_str(metadata.get("status"))
            report.success = _as_bool(event.get("success")) if report.success is None else report.success
            report.final_summary = report.final_summary or _as_str(_get(event, "output_summary"))
            _update_changed_files(report, _as_str_list(metadata.get("changed_files")))
            report.completion_kind = report.completion_kind or _as_str(metadata.get("completion_kind"))
            report.assistant_stop_reason = report.assistant_stop_reason or _as_str(metadata.get("assistant_stop_reason"))
            report.delivery_kind = report.delivery_kind or _delivery_kind_value(event)
            report.task_intent = report.task_intent or _as_str(metadata.get("task_intent"))
            if report.requires_evidence is None:
                report.requires_evidence = _as_bool(metadata.get("requires_evidence"))
            _ordered_extend(report.evidence_reasons, _as_str_list(metadata.get("evidence_reasons")))
            if report.write_attempted is None:
                report.write_attempted = _as_bool(metadata.get("write_attempted"))
            if report.write_executed is None:
                report.write_executed = _as_bool(metadata.get("write_executed"))
            _ordered_extend(report.written_files, _as_str_list(metadata.get("written_files")))
            _ordered_extend(report.observed_changed_files, _as_str_list(metadata.get("observed_changed_files")))
            _ordered_extend(report.claimed_changed_files, _as_str_list(metadata.get("claimed_changed_files")))
            if report.tests_required is None:
                report.tests_required = _as_bool(metadata.get("tests_required"))
            if report.diff_required is None:
                report.diff_required = _as_bool(metadata.get("diff_required"))
            _ordered_extend(report.missing_evidence, _as_str_list(metadata.get("missing_evidence")))
            if report.tests.summary is None:
                report.tests.summary = _as_str(metadata.get("tests")) or report.tests.summary
            continue

        if event_type == "run_end":
            if report.status is None:
                output_summary = _as_str(_get(event, "output_summary"))
                if output_summary == "max_steps_exceeded":
                    report.status = "max_steps_exceeded"
                elif output_summary == "llm_exhausted":
                    report.status = "llm_exhausted"
                elif output_summary == "llm_error":
                    report.status = "llm_error"
                elif _as_bool(event.get("success")) is True:
                    report.status = "success"
                elif _as_bool(event.get("success")) is False:
                    report.status = output_summary or "failed"
            if report.success is None:
                report.success = _as_bool(event.get("success"))
            if report.final_summary is None:
                report.final_summary = _as_str(_get(event, "output_summary"))
            report.completion_kind = report.completion_kind or _as_str(metadata.get("completion_kind"))
            report.assistant_stop_reason = report.assistant_stop_reason or _as_str(metadata.get("assistant_stop_reason"))
            report.delivery_kind = report.delivery_kind or _delivery_kind_value(event)
            report.task_intent = report.task_intent or _as_str(metadata.get("task_intent"))
            if report.requires_evidence is None:
                report.requires_evidence = _as_bool(metadata.get("requires_evidence"))
            _ordered_extend(report.missing_evidence, _as_str_list(metadata.get("missing_evidence")))
            if report.tests_required is None:
                report.tests_required = _as_bool(metadata.get("tests_required"))
            if report.diff_required is None:
                report.diff_required = _as_bool(metadata.get("diff_required"))
            continue

    for pending in pending_policies:
        if pending.policy_decision in {"allow", "ask"} and pending.approved is True:
            pending.executed = False
        if pending.policy_decision is not None:
            _append_warning(report.warnings, f"Policy decision for {pending.tool_name} was not followed by tool_call.")
        report.tool_steps.append(pending)

    if not saw_run_start:
        _append_warning(report.warnings, "Missing run_start event.")
    if not saw_agent_finish:
        _append_warning(report.warnings, "Missing agent_finish event.")
    if report.status == "max_steps_exceeded":
        _append_warning(report.warnings, "Run ended because max_steps was exceeded.")
    if report.tests_required is True and report.tests.status != "passed":
        _append_warning(report.warnings, "Final status is success but no passed run_tests result was found.")
    if report.diff_required is True and not report.diff.checked:
        _append_warning(report.warnings, "git_diff was not checked before report generation.")

    return report
