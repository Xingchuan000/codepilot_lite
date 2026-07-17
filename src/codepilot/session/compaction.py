from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import signature
from typing import Any, Literal, Mapping

from codepilot.session.database import SessionDatabase
from codepilot.session.context import ContextAssembler
from codepilot.session.context_budget import ContextBudgetAllocator, ContextBudgetExceeded, ContextItem, estimate_tokens
from codepilot.session.model_capabilities import ModelContextProfile, resolve_model_context_profile
from codepilot.session.models import ContextSummaryRecord
from codepilot.session.store import SessionStore


@dataclass(frozen=True)
class CompactionResult:
    summary: ContextSummaryRecord
    covered_message_ids: tuple[str, ...]
    retained_message_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompactionOutcome:
    status: Literal["compacted", "already_compacted", "no_compactable_history"]
    summary: ContextSummaryRecord | None
    estimate_before: int
    estimate_after: int


@dataclass(frozen=True)
class ContextEstimate:
    tokens: int
    selected_items: tuple[ContextItem, ...]


@dataclass(frozen=True)
class CompactionSelection:
    covered_message_ids: tuple[str, ...]
    retained_message_ids: tuple[str, ...]
    retained_tool_call_ids: tuple[str, ...]
    source_start_sequence: int
    source_end_sequence: int


@dataclass(frozen=True)
class RetainedFactSet:
    """Compact 保留下来的完整事实集合及其可解释原因。"""

    message_ids: frozenset[str]
    tool_call_ids: frozenset[str]
    tool_result_ids: frozenset[str]
    artifact_ids: frozenset[str]
    event_ids: frozenset[str]
    reasons: Mapping[str, tuple[str, ...]]

    def __iter__(self):
        """兼容上一轮内部 tuple 解包；新代码应直接读取命名字段。"""

        yield set(self.message_ids)
        yield set(self.tool_call_ids)


class MustRetainPolicy:
    """集中定义 Compact 不能覆盖的业务事实。"""

    def __init__(self, store: SessionStore, recent_turn_count: int = 4) -> None:
        self.store = store
        self.recent_turn_count = recent_turn_count

    def select(self, session_id: str, current_turn_id: str | None) -> RetainedFactSet:
        turns = self.store.list_turns(session_id)
        recent_ids = {turn.turn_id for turn in turns[-self.recent_turn_count:]}
        if current_turn_id is not None:
            recent_ids.add(current_turn_id)
        messages = self.store.list_messages_with_parts(session_id)
        retained_messages = {message.message_id for message, _ in messages if message.turn_id in recent_ids}
        reasons: dict[str, list[str]] = {message_id: ["recent_complete_turn"] for message_id in retained_messages}
        retained_calls: set[str] = set()
        retained_results: set[str] = set()
        retained_artifacts: set[str] = set()
        retained_events: set[str] = set()
        calls = self.store.list_tool_calls(session_id)
        messages_by_call = {
            call.tool_call_id: {
                message.message_id
                for message, parts in messages
                if call.message_id == message.message_id
                or message.metadata.get("tool_call_id") == call.tool_call_id
                or any(part.metadata.get("tool_call_id") == call.tool_call_id for part in parts)
            }
            for call in calls
        }

        # 未解决调用和三类最新业务事实必须分别保护，不能用一个全局的最后三条列表互相挤掉。
        unresolved = [call for call in calls if call.status in {"approval_pending", "execution_started", "execution_uncertain", "recovery_required"}]
        categories = {
            "write": {"replace_range", "apply_patch"},
            "test": {"run_tests"},
            "diff": {"git_diff"},
        }
        for call in unresolved:
            _retain_call(self.store, call.tool_call_id, "unresolved_tool_call", messages_by_call, retained_messages, retained_calls, retained_results, retained_artifacts, retained_events, reasons)
        for category, names in categories.items():
            candidates = [call for call in calls if call.tool_name in names or category == "test" and call.tool_name == "run_shell" and _is_test_command(call.arguments)]
            if candidates:
                _retain_call(self.store, candidates[-1].tool_call_id, f"latest_{category}_fact", messages_by_call, retained_messages, retained_calls, retained_results, retained_artifacts, retained_events, reasons)

        for message_id in retained_messages:
            reasons.setdefault(message_id, []).append("current_or_recent_turn")
        for event in self.store.list_events(session_id):
            if event.event_type in {"branch_changed", "permission_resolved", "model_changed"}:
                retained_events.add(event.event_id)
                reasons.setdefault(event.event_id, []).append(f"session_event:{event.event_type}")
        return RetainedFactSet(
            frozenset(retained_messages),
            frozenset(retained_calls),
            frozenset(retained_results),
            frozenset(retained_artifacts),
            frozenset(retained_events),
            {key: tuple(value) for key, value in reasons.items()},
        )


def _is_test_command(arguments: dict[str, Any]) -> bool:
    """只把明确的测试/验证命令归入 TEST 类，不把普通 shell 当测试结果。"""

    command = arguments.get("command") or arguments.get("cmd") or ""
    text = str(command).lower()
    return any(token in text for token in ("pytest", "unittest", "ruff check", "mypy", "npm test", "cargo test"))


def _retain_call(
    store: SessionStore,
    tool_call_id: str,
    reason: str,
    messages_by_call: dict[str, set[str]],
    retained_messages: set[str],
    retained_calls: set[str],
    retained_results: set[str],
    retained_artifacts: set[str],
    retained_events: set[str],
    reasons: dict[str, list[str]],
) -> None:
    """沿 ToolCall → Permission → ToolResult → Artifact/Event 链整体保留事实。"""

    retained_calls.add(tool_call_id)
    for message_id in messages_by_call.get(tool_call_id, set()):
        retained_messages.add(message_id)
        reasons.setdefault(message_id, []).append(reason)
    call = store.get_tool_call(tool_call_id)
    session_id = store.get_session(store.get_turn(call.turn_id).session_id).session_id
    result = store.get_tool_result_by_call(tool_call_id)
    if result is not None:
        retained_results.add(result.tool_result_id)
        reasons.setdefault(result.tool_result_id, []).append(reason)
        if result.artifact_id is not None:
            retained_artifacts.add(result.artifact_id)
            reasons.setdefault(result.artifact_id, []).append(f"{reason}:artifact")
    for request in store.list_permission_requests(session_id):
        if request.tool_call_id == tool_call_id:
            for event in store.list_events(request.session_id or ""):
                if event.payload.get("tool_call_id") == tool_call_id or event.payload.get("request_id") == request.request_id:
                    retained_events.add(event.event_id)
                    reasons.setdefault(event.event_id, []).append(f"{reason}:permission")


class CompactionService:
    """把旧历史压缩成摘要，但不删除原始事实。"""

    def __init__(self, database: SessionDatabase, summarizer: Callable[[list[dict[str, Any]]], str] | None = None, threshold: float = 0.8) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        self.store = SessionStore(database)
        self.summarizer = summarizer or _default_summary
        self.threshold = threshold
        self.planning = ContextPlanningService(database)

    def compact(
        self,
        session_id: str,
        *,
        force: bool = False,
        current_turn_id: str | None = None,
        profile: ModelContextProfile | None = None,
    ) -> CompactionResult:
        session = self.store.get_session(session_id)
        profile = profile or resolve_model_context_profile(session.provider, session.current_model)
        messages = self.store.list_messages_with_parts(session_id)
        latest_summary = self.store.get_latest_context_summary(session_id)
        covered_before = set(latest_summary.metadata.get("covered_message_ids", [])) if latest_summary is not None else set()
        payload = [
            {
                "message_id": message.message_id,
                "turn_id": message.turn_id,
                "role": message.role,
                "status": message.status,
                "content": message.content,
                "parts": [part.content for part in parts if part.replayable],
            }
            for message, parts in messages
            if message.metadata.get("summary_id") is None and message.message_id not in covered_before
        ]
        if not payload:
            raise ContextBudgetExceeded("no history can be compacted", reason="no_compactable_history")
        # 是否达到 Compact 阈值只由 ensure_context_budget 的有效 ContextPlan 决定；这里
        # 不能再按摘要输入 payload 的原始长度判断，否则会把 System/当前 Turn 的预算
        # 压力错误地映射成“低于阈值”。

        retained = MustRetainPolicy(self.store).select(session_id, current_turn_id)
        retained_message_ids = set(retained.message_ids)
        retained_tool_call_ids = set(retained.tool_call_ids)
        covered_message_ids = [message.message_id for message, _ in messages if message.message_id not in retained_message_ids and message.message_id not in covered_before and message.metadata.get("summary_id") is None]
        summary_payload = [item for item in payload if item["message_id"] in covered_message_ids]
        if not summary_payload:
            raise ContextBudgetExceeded("no new history is available for compaction", reason="no_compactable_history")
        try:
            previous_summary = latest_summary.content if latest_summary is not None else None
            max_output_tokens = max(1, min(4_000, int(profile.max_input_tokens * 0.1)))
            summary_input = ([{"role": "system", "content": previous_summary}] if previous_summary else []) + summary_payload
            if "max_output_tokens" in signature(self.summarizer).parameters:
                summary_text = self.summarizer(summary_input, max_output_tokens=max_output_tokens, previous_summary=previous_summary)
            else:
                summary_text = self.summarizer(summary_input)
            if estimate_tokens(summary_text) > max_output_tokens:
                raise ContextBudgetExceeded("compaction summary exceeds its output budget", reason="summary_output_overflow")
            _validate_summary(summary_text)
        except Exception as exc:
            self.store.append_event(
                session_id=session_id,
                event_type="context_compaction_failed",
                payload={"error": str(exc), "message_count": len(summary_payload)},
                turn_id=current_turn_id,
                metadata={"source": "compaction_service"},
            )
            raise
        newly_covered_message_ids = tuple(covered_message_ids)
        covered_message_ids = list(dict.fromkeys([*(latest_summary.metadata.get("covered_message_ids", []) if latest_summary else []), *covered_message_ids]))
        all_sequences = [self.store.get_turn(message.turn_id).sequence for message, _ in messages if message.message_id in covered_message_ids]
        if latest_summary is not None and latest_summary.source_start_sequence is not None:
            all_sequences.append(latest_summary.source_start_sequence)
        if latest_summary is not None and latest_summary.source_end_sequence is not None:
            all_sequences.append(latest_summary.source_end_sequence)
        selection = CompactionSelection(
            tuple(covered_message_ids),
            tuple(sorted(retained_message_ids)),
            tuple(sorted(retained_tool_call_ids)),
            min(all_sequences),
            max(all_sequences),
        )
        metadata = {
            "covered_message_ids": list(selection.covered_message_ids),
            "newly_covered_message_ids": list(newly_covered_message_ids),
            "supersedes_summary_id": latest_summary.summary_id if latest_summary is not None else None,
            "retained_message_ids": list(selection.retained_message_ids),
            "retained_tool_call_ids": list(selection.retained_tool_call_ids),
            "retained_tool_result_ids": sorted(retained.tool_result_ids),
            "retained_artifact_ids": sorted(retained.artifact_ids),
            "retained_event_ids": sorted(retained.event_ids),
            "retained_reasons": {key: list(value) for key, value in retained.reasons.items()},
            "provider": profile.provider,
            "model": profile.model,
        }
        event_payload = {
            "covered_message_count": len(covered_message_ids),
            "retained_message_count": len(retained_message_ids),
            "retained_tool_call_count": len(retained_tool_call_ids),
            "force": force,
        }
        try:
            summary_record = self.store.replace_context_summary(
                session_id=session_id,
                previous_summary_id=latest_summary.summary_id if latest_summary is not None else None,
                summary_content=summary_text,
                turn_id=current_turn_id,
                source_start_sequence=selection.source_start_sequence,
                source_end_sequence=selection.source_end_sequence,
                model=profile.model,
                metadata=metadata,
                event_payload=event_payload,
            )
        except Exception as exc:
            self.store.append_event(
                session_id=session_id,
                event_type="context_compaction_failed",
                payload={"error": str(exc), "message_count": len(summary_payload)},
                turn_id=current_turn_id,
                metadata={"source": "compaction_service"},
            )
            raise
        return CompactionResult(summary_record, selection.covered_message_ids, selection.retained_message_ids)

    def ensure_context_budget(self, session_id: str, current_turn_id: str, profile: ModelContextProfile) -> CompactionOutcome:
        """按有效 ContextPlan 判断是否需要 Compact，而不是重复统计 SQLite 原文。"""

        before = self.planning.estimate_effective_context(session_id, current_turn_id, profile)
        threshold_tokens = profile.max_input_tokens * self.threshold
        latest = self.store.get_latest_context_summary(session_id)
        if before.tokens < threshold_tokens:
            return CompactionOutcome("already_compacted" if latest is not None else "no_compactable_history", latest, before.tokens, before.tokens)
        try:
            result = self.compact(session_id, force=False, current_turn_id=current_turn_id, profile=profile)
        except ContextBudgetExceeded as exc:
            if exc.reason == "no_compactable_history":
                # ContextPlan 已经证明必需集合可以放入窗口；没有可压缩旧历史时，
                # 这是“已经压缩到当前安全边界”，不是运行时错误。
                return CompactionOutcome("already_compacted" if latest is not None else "no_compactable_history", latest, before.tokens, before.tokens)
            raise
        after = self.planning.estimate_effective_context(session_id, current_turn_id, profile)
        if after.tokens > profile.max_input_tokens:
            raise ContextBudgetExceeded("context remains over budget after compaction", reason="post_compaction_overflow")
        return CompactionOutcome("compacted", result.summary, before.tokens, after.tokens)


class ContextPlanningService:
    """以最终 ContextPlan 估算当前有效上下文。"""

    def __init__(self, database: SessionDatabase) -> None:
        self.assembler = ContextAssembler(database)

    def estimate_effective_context(self, session_id: str, current_turn_id: str, profile: ModelContextProfile) -> ContextEstimate:
        plan = self.assembler.build_plan(session_id, current_turn_id, profile.provider, profile.model, profile=profile)
        allocator = ContextBudgetAllocator(profile.max_input_tokens, protocol_overhead_tokens=profile.protocol_overhead_tokens)
        selected: list[ContextItem] = []
        for item in plan.system_items + plan.current_turn_items:
            allocator.require(item)
            selected.append(item)
        for item in plan.summary_items + plan.history_items:
            if item.mandatory:
                allocator.require(item)
                selected.append(item)
            elif allocator.try_add(item):
                selected.append(item)
        return ContextEstimate(allocator.used_tokens + profile.protocol_overhead_tokens, tuple(selected))

def _default_summary(messages: list[dict[str, Any]], *, max_output_tokens: int = 4_000, previous_summary: str | None = None) -> str:
    lines = ["Session summary:"]
    if previous_summary:
        lines.append(f"Previous summary: {previous_summary[:1000]}")
    for message in messages:
        lines.append(f"- {message['role']}: {str(message['content'])[:800]}")
    lines.extend(["Key decisions: preserved in the summarized messages.", "Files/tests/diff: see the listed tool results.", "Unfinished work: continue from the latest message."])
    return "\n".join(lines)[: max_output_tokens * 4]


def _validate_summary(summary: str) -> None:
    if not summary.strip():
        raise ValueError("compaction summary is empty")
    required = ("Key decisions", "Files/tests/diff", "Unfinished work")
    if any(item not in summary for item in required):
        raise ValueError("compaction summary does not cover required fields")
