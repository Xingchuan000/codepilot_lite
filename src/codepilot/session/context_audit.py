from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.memory.policy import redact_memory_value
from codepilot.session.context_adapters import PreparedContext
from codepilot.session.context_budget import estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.ids import now_iso


@dataclass(frozen=True)
class ContextMessageManifestItem:
    ordinal: int
    role: str
    source_kind: str
    source_id: str | None
    context_key: str | None
    estimated_tokens: int
    sha256: str
    preview: str


@dataclass(frozen=True)
class ContextMessageSource:
    role: str
    source_kind: str
    source_id: str | None
    context_key: str | None


@dataclass(frozen=True)
class ContextCompactionSnapshot:
    snapshot_id: str
    session_id: str
    turn_id: str
    attempt_id: str | None
    step: int | None
    trigger: str
    scope: str
    status: str
    summary_id: str | None
    checkpoint_id: str | None
    estimated_tokens_before: int
    estimated_tokens_after: int
    selected_context_keys: tuple[str, ...]
    omitted_context_keys: tuple[str, ...]
    covered_message_ids: tuple[str, ...]
    retained_message_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    instruction_ids: tuple[str, ...]
    message_manifest: tuple[ContextMessageManifestItem, ...]
    redacted_preview: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


class ContextAuditRepository:
    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def record(
        self,
        *,
        session_id: str,
        turn_id: str,
        trigger: str,
        scope: str,
        status: str,
        estimated_tokens_before: int,
        estimated_tokens_after: int,
        max_input_tokens: int,
        protocol_overhead_tokens: int = 0,
        attempt_id: str | None = None,
        step: int | None = None,
        summary_id: str | None = None,
        checkpoint_id: str | None = None,
        prepared: PreparedContext | None = None,
        messages: list[ChatMessage | RichChatMessage] | None = None,
        message_sources: tuple[ContextMessageSource, ...] = (),
        covered_message_ids: tuple[str, ...] = (),
        retained_message_ids: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> ContextCompactionSnapshot:
        snapshot_id = f"context-snapshot-{uuid4().hex}"
        selected = prepared.selected_items if prepared is not None else ()
        omitted = prepared.omitted_items if prepared is not None else ()
        manifest = _manifest(prepared, messages or (prepared.messages if prepared is not None else []), message_sources)
        previews = tuple(item.preview for item in manifest)
        memory_ids = tuple(dict.fromkeys(source_id for item in selected if item.source_kind == "memory" for source_id in item.source_ids))
        instruction_ids = tuple(dict.fromkeys(source_id for item in selected if item.source_kind == "instruction" for source_id in item.source_ids))
        values = {
            "selected": [item.key for item in selected],
            "omitted": [item.key for item in omitted],
            "covered": list(covered_message_ids),
            "retained": list(retained_message_ids),
            "memory": list(memory_ids),
            "instruction": list(instruction_ids),
            "manifest": [item.__dict__ for item in manifest],
            "preview": list(previews),
            "metadata": dict(redact_memory_value(metadata or {}).value),
        }
        created_at = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO context_compaction_snapshots(
                    snapshot_id, session_id, turn_id, attempt_id, step, trigger, scope, status,
                    summary_id, checkpoint_id, estimated_tokens_before, estimated_tokens_after,
                    protocol_overhead_tokens, max_input_tokens, selected_context_keys_json,
                    omitted_context_keys_json, covered_message_ids_json, retained_message_ids_json,
                    memory_ids_json, instruction_ids_json, message_manifest_json,
                    redacted_preview_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, session_id, turn_id, attempt_id, step, trigger, scope, status,
                    summary_id, checkpoint_id, estimated_tokens_before, estimated_tokens_after,
                    protocol_overhead_tokens, max_input_tokens, *(_dump(values[key]) for key in ("selected", "omitted", "covered", "retained", "memory", "instruction", "manifest", "preview", "metadata")), created_at,
                ),
            )
        return self.get(snapshot_id)

    def get(self, snapshot_id: str) -> ContextCompactionSnapshot:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM context_compaction_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise LookupError(snapshot_id)
        return _snapshot(row)

    def list_for_turn(self, turn_id: str) -> tuple[ContextCompactionSnapshot, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM context_compaction_snapshots WHERE turn_id = ? ORDER BY created_at, snapshot_id", (turn_id,)).fetchall()
        return tuple(_snapshot(row) for row in rows)


def _manifest(
    prepared: PreparedContext | None,
    messages: list[ChatMessage | RichChatMessage],
    message_sources: tuple[ContextMessageSource, ...],
) -> tuple[ContextMessageManifestItem, ...]:
    sources = [(item.source_kind, item.source_id, item.context_key) for item in message_sources]
    if not sources and prepared is not None:
        for item in prepared.selected_items:
            sources.extend((item.source_kind or "generated", item.source_ids[0] if item.source_ids else None, item.key) for _ in item.messages)
    result = []
    for ordinal, message in enumerate(messages):
        text = message.content if isinstance(message, ChatMessage) else json.dumps([part.content for part in message.parts], ensure_ascii=False, default=str)
        source_kind, source_id, context_key = sources[ordinal] if ordinal < len(sources) else ("message", None, None)
        preview = str(redact_memory_value(text[:200]).value)[:200]
        result.append(ContextMessageManifestItem(ordinal, message.role, source_kind, source_id, context_key, estimate_tokens(message), hashlib.sha256(text.encode()).hexdigest(), preview))
    return tuple(result)


def _snapshot(row: Any) -> ContextCompactionSnapshot:
    return ContextCompactionSnapshot(
        snapshot_id=row["snapshot_id"], session_id=row["session_id"], turn_id=row["turn_id"], attempt_id=row["attempt_id"], step=row["step"], trigger=row["trigger"], scope=row["scope"], status=row["status"], summary_id=row["summary_id"], checkpoint_id=row["checkpoint_id"], estimated_tokens_before=row["estimated_tokens_before"], estimated_tokens_after=row["estimated_tokens_after"], selected_context_keys=tuple(json.loads(row["selected_context_keys_json"])), omitted_context_keys=tuple(json.loads(row["omitted_context_keys_json"])), covered_message_ids=tuple(json.loads(row["covered_message_ids_json"])), retained_message_ids=tuple(json.loads(row["retained_message_ids_json"])), memory_ids=tuple(json.loads(row["memory_ids_json"])), instruction_ids=tuple(json.loads(row["instruction_ids_json"])), message_manifest=tuple(ContextMessageManifestItem(**item) for item in json.loads(row["message_manifest_json"])), redacted_preview=tuple(json.loads(row["redacted_preview_json"])), metadata=json.loads(row["metadata_json"]), created_at=row["created_at"]
    )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
