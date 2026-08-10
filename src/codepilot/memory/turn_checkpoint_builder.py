from __future__ import annotations

import hashlib
from typing import Any

from codepilot.memory.models import TurnCheckpointContent, TurnMemoryCheckpoint
from codepilot.memory.test_scope import TestScope, exact_command_scope, parse_test_scope
from codepilot.session.database import SessionDatabase
from codepilot.session.models import ToolCallRecord, ToolResultRecord
from codepilot.session.repositories import SessionRepositories
from codepilot.tools.test_tools import looks_like_pytest_command

READ_TOOLS = {"read_file"}
WRITE_TOOLS = {"replace_range", "apply_patch", "write_file"}
COMMAND_TOOLS = {"run_tests", "run_shell"}
EVIDENCE_KEYS = {
    "requires_evidence",
    "reasons",
    "write_attempted",
    "write_executed",
    "written_files",
    "observed_changed_files",
    "claimed_changed_files",
    "tests_required",
    "last_test_command",
    "last_failed_tests",
    "diff_required",
    "diff_checked",
    "missing",
}
SMALL_ARGUMENT_KEYS = {"path", "start_line", "end_line", "offset", "limit", "max_entries", "staged", "dry_run"}
BOUNDED_ARGUMENT_KEYS = {"command": 300, "query": 300, "pattern": 200}
LARGE_ARGUMENT_KEYS = {"patch", "replacement", "content", "body", "payload", "input"}


class TurnCheckpointBuilder:
    def __init__(self, database: SessionDatabase) -> None:
        self.store = SessionRepositories(database)

    def build(
        self,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str | None,
        step: int,
        task: str,
        evidence: dict[str, Any],
        covered_message_ids: tuple[str, ...],
        previous: TurnMemoryCheckpoint | None,
    ) -> TurnCheckpointContent:
        prior = TurnCheckpointContent.from_dict(previous.content) if previous is not None else TurnCheckpointContent()
        calls = [call for call in self.store.tool_executions.list_tool_calls(session_id) if call.turn_id == turn_id]
        results = {call.tool_call_id: self.store.tool_executions.get_tool_result_by_call(call.tool_call_id) for call in calls}
        messages = tuple((message, tuple(parts)) for message, parts in self.store.messages.list_messages_with_parts(session_id, turn_id))
        files_read = list(prior.files_read)
        files_modified = list(prior.files_modified)
        commands = list(prior.commands_run)
        tests = list(prior.test_results)
        facts: list[dict[str, Any]] = []
        covered = set(covered_message_ids)
        result_message_ids = {
            str(message.metadata["tool_call_id"]): message.message_id
            for message, _ in messages
            if message.role == "tool" and message.metadata.get("tool_call_id") is not None
        }
        for call in calls:
            result = results[call.tool_call_id]
            metadata = result.metadata if result is not None else {}
            if call.tool_name in READ_TOOLS and (call.arguments.get("path") or metadata.get("path")):
                files_read.append(str(call.arguments.get("path") or metadata["path"]))
            if call.tool_name in WRITE_TOOLS and call.arguments.get("path"):
                files_modified.append(str(call.arguments["path"]))
            files_modified.extend(_strings(metadata.get("changed_files")))
            files_modified.extend(_strings(metadata.get("touched_paths")))
            if call.tool_name in COMMAND_TOOLS:
                command = call.arguments.get("command") or metadata.get("command")
                if command:
                    commands.append(str(command))
            if _is_test_call(call) and result is not None:
                tests.append(_test_fact(result.status, result.success, metadata))
            if result is not None and (
                call.message_id in covered or result_message_ids.get(call.tool_call_id) in covered
            ):
                facts.append(
                    {
                        "tool_call_id": call.tool_call_id,
                        "tool_name": call.tool_name,
                        "success": result.success,
                        "status": result.status,
                        **_selected_metadata(metadata),
                    }
                )
        pending = tuple(
            {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.tool_name,
                "arguments": _summarize_pending_arguments(call.tool_name, call.arguments),
                "status": call.status,
            }
            for call in self.store.tool_executions.list_unresolved_tool_calls(turn_id)[-8:]
        )
        active_errors = _active_tool_errors(calls, results, evidence)
        safe_evidence = _bounded_evidence(evidence)
        return TurnCheckpointContent(
            current_goal=str(task)[:800],
            current_step=step,
            user_constraints=_bounded_unique([*prior.user_constraints, *_strings(evidence.get("user_constraints"))]),
            confirmed_decisions=_bounded_unique([*prior.confirmed_decisions, *_strings(evidence.get("confirmed_decisions"))]),
            files_read=_bounded_unique(files_read),
            files_modified=_bounded_unique(files_modified),
            commands_run=_bounded_unique(commands),
            test_results=_bounded_unique(tests),
            current_errors=_bounded_unique(active_errors, limit=8),
            pending_tool_calls=pending,
            recent_tool_facts=tuple(facts[-10:]),
            next_step=_next_step(pending, safe_evidence, bool(active_errors))[:400],
            evidence=safe_evidence,
            fallback_previews=tuple(item[:240] for item in prior.fallback_previews[-8:]),
        )


def _active_tool_errors(
    calls: list[ToolCallRecord],
    results: dict[str, ToolResultRecord | None],
    evidence: dict[str, Any],
) -> tuple[str, ...]:
    active: dict[str, str] = {}
    for call in calls:
        result = results[call.tool_call_id]
        if result is None:
            continue
        key = _tool_error_key(call)
        if result.success is False or result.error:
            error = str(result.error or result.metadata.get("summary_line") or result.status)[:240]
            active[key] = f"[{key[5:]}] {error}" if key.startswith("test:") else error
        elif result.success is True:
            command = str(call.arguments.get("command") or result.metadata.get("command") or "")
            scope = _scope_for_call(call, command)
            if scope is not None and scope.executing and scope.full_suite:
                active = {error_key: value for error_key, value in active.items() if not error_key.startswith("test:pytest:")}
            elif scope is not None and scope.executing:
                active.pop(key, None)
    return tuple(active.values())


def _tool_error_key(call: ToolCallRecord) -> str:
    if _is_test_call(call):
        command = str(call.arguments.get("command", ""))
        scope = _scope_for_call(call, command)
        return f"test:{scope.key if scope is not None else _normalize_command(command)}"
    path = str(call.arguments.get("path", ""))
    if call.tool_name in READ_TOOLS:
        return f"read:{path}"
    if call.tool_name in WRITE_TOOLS:
        return f"write:{path or _argument_hash(call.arguments)}"
    if call.tool_name == "run_shell":
        return f"shell:{_normalize_command(str(call.arguments.get('command', '')))}"
    return f"tool:{call.tool_name}"


def _is_test_call(call: ToolCallRecord) -> bool:
    return call.tool_name == "run_tests" or (
        call.tool_name == "run_shell"
        and isinstance(call.arguments.get("command"), str)
        and looks_like_pytest_command(call.arguments["command"])
    )


def _normalize_command(command: str) -> str:
    return " ".join(command.split())[:300]


def _scope_for_call(call: ToolCallRecord, command: str) -> TestScope | None:
    if call.tool_name != "run_tests" and not _is_test_call(call):
        return None
    return parse_test_scope(command) or exact_command_scope(command)


def _summarize_pending_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    summary = {key: arguments[key] for key in SMALL_ARGUMENT_KEYS if key in arguments}
    for key, limit in BOUNDED_ARGUMENT_KEYS.items():
        if key in arguments:
            summary[key] = str(arguments[key])[:limit]
    for key in LARGE_ARGUMENT_KEYS:
        if key not in arguments:
            continue
        text = str(arguments[key])
        summary[f"{key}_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        summary[f"{key}_chars"] = len(text)
    summary["argument_keys"] = sorted(str(key) for key in arguments)
    if tool_name not in WRITE_TOOLS:
        for key, value in arguments.items():
            if key not in summary and key not in LARGE_ARGUMENT_KEYS and key not in BOUNDED_ARGUMENT_KEYS:
                text = str(value)
                summary[f"{key}_sha256"] = hashlib.sha256(text.encode()).hexdigest()
                summary[f"{key}_chars"] = len(text)
    return summary


def _bounded_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in EVIDENCE_KEYS:
        source_key = key
        if key == "reasons" and key not in evidence:
            source_key = "evidence_reasons"
        if key == "missing" and key not in evidence:
            source_key = "missing_evidence"
        if source_key not in evidence:
            continue
        value = evidence[source_key]
        if isinstance(value, (list, tuple)):
            result[key] = [str(item)[:240] for item in value[:20]]
        elif isinstance(value, str):
            result[key] = value[:240]
        elif isinstance(value, (bool, int, float)) or value is None:
            result[key] = value
    return result


def _strings(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)] if value else []


def _bounded_unique(values: list[str] | tuple[str, ...], *, limit: int = 20) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value)[:240] for value in values if value))[-limit:]


def _selected_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = ("command", "returncode", "status", "summary_line", "failed_tests", "changed_files", "touched_paths", "path")
    result: dict[str, Any] = {}
    for key in keys:
        if key not in metadata:
            continue
        value = metadata[key]
        result[key] = [str(item)[:240] for item in value[:20]] if isinstance(value, (list, tuple)) else str(value)[:300]
    return result


def _test_fact(status: str, success: bool | None, metadata: dict[str, Any]) -> str:
    summary = metadata.get("summary_line") or metadata.get("status") or status
    command = metadata.get("command")
    return f"{command}: {summary}" if command else str(summary if success is not None else status)


def _argument_hash(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(str(sorted(arguments)).encode()).hexdigest()


def _next_step(pending: tuple[dict[str, Any], ...], evidence: dict[str, Any], has_errors: bool) -> str:
    if pending:
        return "Resolve or reconcile the pending tool call before continuing."
    missing = set(_strings(evidence.get("missing")))
    if "missing_passed_tests" in missing:
        return "Run the relevant tests and obtain a passing result."
    if "missing_diff_check" in missing:
        return "Inspect the final git diff."
    if has_errors:
        return "Address the latest tool failure."
    return ""
