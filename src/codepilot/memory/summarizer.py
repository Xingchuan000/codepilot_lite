from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from codepilot.llm.types import ChatMessage, CodePilotLLMClient
from codepilot.memory.models import SessionSummaryContent
from codepilot.memory.policy import redact_memory_value, sanitize_memory_content
from codepilot.session.context_budget import estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.repositories import SessionRepositories

_LIMITS = {
    "user_constraints": (12, 300),
    "confirmed_decisions": (16, 400),
    "repository_facts": (20, 400),
    "files_read": (40, 300),
    "files_modified": (30, 300),
    "commands_run": (20, 500),
    "test_results": (20, 500),
    "errors_and_failures": (16, 500),
    "unresolved_work": (12, 500),
    "next_actions": (12, 500),
}
_READ_TOOLS = {"read_file", "list_files", "search_files", "find_files", "rg"}
_WRITE_TOOLS = {"apply_patch", "replace_range", "write_file"}


@dataclass(frozen=True)
class SessionSummaryEvidence:
    messages: tuple[dict[str, Any], ...]
    commands_run: tuple[str, ...]
    test_results: tuple[str, ...]
    files_read: tuple[str, ...]
    files_modified: tuple[str, ...]
    errors_and_failures: tuple[str, ...]
    diff_status: dict[str, Any]
    branch: str
    commit: str


class SessionSummaryEvidenceCollector:
    def __init__(self, database: SessionDatabase) -> None:
        self.store = SessionRepositories(database)

    def collect(
        self,
        session_id: str,
        covered_message_ids: tuple[str, ...],
        current_turn_id: str | None,
    ) -> SessionSummaryEvidence:
        covered = set(covered_message_ids)
        messages = tuple(
            {
                "message_id": message.message_id,
                "turn_id": message.turn_id,
                "role": message.role,
                "content": str(message.content)[:2_000],
            }
            for message, _ in self.store.messages.list_messages_with_parts(session_id)
            if message.message_id in covered
        )
        turn_ids = {message["turn_id"] for message in messages}
        commands: list[str] = []
        tests: list[str] = []
        files_read: list[str] = []
        files_modified: list[str] = []
        errors: list[str] = []
        diff_status: dict[str, Any] = {}
        for call in self.store.tool_executions.list_tool_calls(session_id):
            if call.turn_id not in turn_ids:
                continue
            command = call.arguments.get("command") or call.arguments.get("cmd")
            if call.tool_name in {"run_shell", "run_tests"} and isinstance(command, str):
                commands.append(command)
            paths = _paths(call.arguments)
            if call.tool_name in _READ_TOOLS:
                files_read.extend(paths)
            if call.tool_name in _WRITE_TOOLS:
                files_modified.extend(paths)
            result = self.store.tool_executions.get_tool_result_by_call(call.tool_call_id)
            if result is not None and (call.tool_name == "run_tests" or _is_test_command(command)):
                preview = result.output_preview or result.error or str(result.content)
                tests.append(f"{call.tool_name}: {'success' if result.success else 'failed'}: {preview[:400]}")
            if result is not None and (result.success is False or result.error):
                errors.append(f"{call.tool_name}: {(result.error or result.output_preview or str(result.content))[:450]}")
            if call.tool_name == "git_diff" and result is not None:
                preview = result.output_preview or str(result.content)
                diff_status = {"present": bool(preview.strip()), "summary": preview[:400]}
        for event in self.store.events.list_events(session_id):
            if event.turn_id in turn_ids and ("error" in event.event_type or "failed" in event.event_type):
                errors.append(f"{event.event_type}: {str(event.payload.get('error', event.payload))[:450]}")
        session = self.store.sessions.get_session(session_id)
        turn = self.store.turns.get_turn(current_turn_id) if current_turn_id is not None else None
        return SessionSummaryEvidence(
            messages,
            _merge_strings(commands, limit=20, item_limit=500),
            _merge_strings(tests, limit=20, item_limit=500),
            _merge_strings(files_read, limit=40, item_limit=300),
            _merge_strings(files_modified, limit=30, item_limit=300),
            _merge_strings(errors, limit=16, item_limit=500),
            diff_status,
            (turn.branch_snapshot if turn is not None else session.current_branch) or "",
            str((turn.metadata if turn is not None else session.metadata).get("commit", "")),
        )


class StructuredSummaryGenerator:
    """Deterministic fallback built exclusively from persisted evidence."""

    def __call__(
        self,
        evidence: SessionSummaryEvidence,
        *,
        max_output_tokens: int = 4_000,
        previous_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence, _ = sanitize_summary_evidence(evidence)
        safe_previous, _ = sanitize_memory_content(previous_summary or {})
        previous = SessionSummaryContent.from_dict(safe_previous) if safe_previous else SessionSummaryContent()
        user_messages = [item for item in evidence.messages if item.get("role") == "user"]
        value = SessionSummaryContent(
            task_goal=str(user_messages[-1].get("content", ""))[:1000] if user_messages else previous.task_goal,
            user_constraints=previous.user_constraints,
            confirmed_decisions=previous.confirmed_decisions,
            repository_facts=previous.repository_facts,
            files_read=_field_merge("files_read", previous.files_read, evidence.files_read),
            files_modified=_field_merge("files_modified", previous.files_modified, evidence.files_modified),
            commands_run=_field_merge("commands_run", previous.commands_run, evidence.commands_run),
            test_results=_field_merge("test_results", previous.test_results, evidence.test_results),
            diff_status=evidence.diff_status or previous.diff_status,
            errors_and_failures=_field_merge("errors_and_failures", previous.errors_and_failures, evidence.errors_and_failures),
            # Deterministic evidence cannot prove that user-level work is resolved.
            unresolved_work=previous.unresolved_work,
            next_actions=previous.next_actions,
            branch=evidence.branch or previous.branch,
            commit=evidence.commit or previous.commit,
            source_message_ids=_merge_strings(
                previous.source_message_ids,
                (str(item["message_id"]) for item in evidence.messages if item.get("message_id")),
                limit=10_000,
                item_limit=100,
            ),
        ).to_dict()
        return _fit_budget(value, max_output_tokens)


class LLMSummaryGenerator:
    def __init__(self, llm: CodePilotLLMClient) -> None:
        self.llm = llm

    def __call__(
        self,
        evidence: SessionSummaryEvidence,
        *,
        max_output_tokens: int = 4_000,
        previous_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence, _ = sanitize_summary_evidence(evidence)
        safe_previous, _ = sanitize_memory_content(previous_summary or {})
        response = self.llm.complete(
            [
                ChatMessage(
                    "system",
                    "Return exactly one JSON object matching the supplied schema. Infer only task_goal, "
                    "user_constraints, confirmed_decisions, repository_facts, unresolved_work and next_actions "
                    "from conversation. Never invent files, commands, tests, Git facts, or secrets. Preserve "
                    "still-valid previous facts and omit uncertain claims. unresolved_work and next_actions "
                    "are complete current-state snapshots: remove resolved old items and return an empty array "
                    "when no unresolved work or next action remains.",
                ),
                ChatMessage(
                    "user",
                    json.dumps(
                        {
                            "previous_summary": safe_previous or None,
                            "deterministic_evidence": _evidence_dict(evidence),
                            "conversation_excerpt": evidence.messages,
                            "output_limits": _LIMITS,
                            "merge_semantics": {
                                "cumulative_fields": [
                                    "user_constraints",
                                    "confirmed_decisions",
                                    "repository_facts",
                                    "files_read",
                                    "files_modified",
                                    "commands_run",
                                    "test_results",
                                    "errors_and_failures",
                                    "source_message_ids",
                                ],
                                "snapshot_fields": ["task_goal", "unresolved_work", "next_actions"],
                            },
                            "schema": SessionSummaryContent().to_dict(),
                            "max_output_tokens": max_output_tokens,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        proposed = SessionSummaryContent.from_dict(json.loads(response.content))
        proposed = SessionSummaryContent.from_dict(sanitize_memory_content(proposed.to_dict())[0])
        previous = SessionSummaryContent.from_dict(safe_previous) if safe_previous else SessionSummaryContent()
        return _fit_budget(merge_llm_summary(previous, proposed, evidence).to_dict(), max_output_tokens)


def merge_llm_summary(
    previous: SessionSummaryContent,
    proposed: SessionSummaryContent,
    evidence: SessionSummaryEvidence,
) -> SessionSummaryContent:
    return SessionSummaryContent(
        task_goal=proposed.task_goal.strip() or _latest_user_goal(evidence) or previous.task_goal,
        user_constraints=_field_merge("user_constraints", previous.user_constraints, proposed.user_constraints),
        confirmed_decisions=_field_merge("confirmed_decisions", previous.confirmed_decisions, proposed.confirmed_decisions),
        repository_facts=_field_merge("repository_facts", previous.repository_facts, proposed.repository_facts),
        files_read=_field_merge("files_read", previous.files_read, evidence.files_read),
        files_modified=_field_merge("files_modified", previous.files_modified, evidence.files_modified),
        commands_run=_field_merge("commands_run", previous.commands_run, evidence.commands_run),
        test_results=_field_merge("test_results", previous.test_results, evidence.test_results),
        diff_status=evidence.diff_status or previous.diff_status,
        errors_and_failures=_field_merge("errors_and_failures", previous.errors_and_failures, evidence.errors_and_failures),
        unresolved_work=_field_merge("unresolved_work", proposed.unresolved_work),
        next_actions=_field_merge("next_actions", proposed.next_actions),
        branch=evidence.branch or previous.branch,
        commit=evidence.commit or previous.commit,
        source_message_ids=_merge_strings(
            previous.source_message_ids,
            (str(item["message_id"]) for item in evidence.messages if item.get("message_id")),
            limit=10_000,
            item_limit=100,
        ),
    )


def _latest_user_goal(evidence: SessionSummaryEvidence) -> str:
    return next(
        (
            str(message.get("content", "")).strip()
            for message in reversed(evidence.messages)
            if message.get("role") == "user" and str(message.get("content", "")).strip()
        ),
        "",
    )


def sanitize_summary_evidence(evidence: SessionSummaryEvidence) -> tuple[SessionSummaryEvidence, int]:
    result = redact_memory_value(
        {
            "messages": evidence.messages,
            "commands_run": evidence.commands_run,
            "test_results": evidence.test_results,
            "files_read": evidence.files_read,
            "files_modified": evidence.files_modified,
            "errors_and_failures": evidence.errors_and_failures,
            "diff_status": evidence.diff_status,
            "branch": evidence.branch,
            "commit": evidence.commit,
        }
    )
    value, count = dict(result.value), result.redaction_count
    return (
        SessionSummaryEvidence(
            tuple(value["messages"]),
            tuple(value["commands_run"]),
            tuple(value["test_results"]),
            tuple(value["files_read"]),
            tuple(value["files_modified"]),
            tuple(value["errors_and_failures"]),
            dict(value["diff_status"]),
            str(value["branch"]),
            str(value["commit"]),
        ),
        count,
    )


def _paths(arguments: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        value
        for key in ("path", "file", "file_path", "target")
        if isinstance((value := arguments.get(key)), str) and value
    )


def _is_test_command(command: Any) -> bool:
    text = str(command).lower()
    return any(value in text for value in ("pytest", "unittest", "npm test", "cargo test", "ruff check", "mypy"))


def _merge_strings(*groups: Iterable[str], limit: int, item_limit: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item).strip()[:item_limit] for group in groups for item in group if str(item).strip()))[-limit:]


def _field_merge(field: str, *groups: Iterable[str]) -> tuple[str, ...]:
    limit, item_limit = _LIMITS[field]
    return _merge_strings(*groups, limit=limit, item_limit=item_limit)


def _evidence_dict(evidence: SessionSummaryEvidence) -> dict[str, Any]:
    return {
        "commands_run": evidence.commands_run,
        "test_results": evidence.test_results,
        "files_read": evidence.files_read,
        "files_modified": evidence.files_modified,
        "errors_and_failures": evidence.errors_and_failures,
        "diff_status": evidence.diff_status,
        "branch": evidence.branch,
        "commit": evidence.commit,
    }


def _fit_budget(value: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
    for field in (
        "repository_facts",
        "files_read",
        "next_actions",
        "commands_run",
        "test_results",
        "errors_and_failures",
        "user_constraints",
        "files_modified",
        "confirmed_decisions",
        "unresolved_work",
        "source_message_ids",
    ):
        while value[field] and estimate_tokens(value) > max_output_tokens:
            value[field].pop(0)
    if estimate_tokens(value) > max_output_tokens:
        value["task_goal"] = str(value["task_goal"])[: max(0, max_output_tokens * 2)]
    if estimate_tokens(value) > max_output_tokens:
        value["diff_status"] = {}
        value["branch"] = ""
        value["commit"] = ""
    if estimate_tokens(value) > max_output_tokens:
        raise ValueError("summary cannot fit output budget")
    return SessionSummaryContent.from_dict(value).to_dict()
