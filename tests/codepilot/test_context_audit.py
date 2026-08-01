from pathlib import Path

from codepilot.llm.types import ChatMessage
from codepilot.memory.turn_window import TurnContextWindow
from codepilot.session.context_audit import ContextAuditRepository
from codepilot.session.context_budget import ContextItem, estimate_tokens
from codepilot.session.database import SCHEMA_VERSION, SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore


def test_turn_compaction_records_small_redacted_audit_snapshot(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="tiny", permission_mode="manual")
    turn = store.create_turn(session_id=session.session_id, title="audit", provider_snapshot="openai", model_snapshot="tiny", permission_mode_snapshot="manual", branch_snapshot=None)
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="inspect")
    dynamic = []
    for index in range(4):
        assistant = ChatMessage("assistant", f'{{"type":"tool_call","tool_name":"read_file","arguments":{{"path":"{index}.py"}}}}')
        observation = ChatMessage("user", f"Tool: read_file\nOutput: token=secret-value-{index}\n" + "x" * 1200)
        dynamic.extend((assistant, observation))
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content=assistant.content)
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content=observation.content)
    TurnContextWindow(database, ModelContextProfile("openai", "tiny", 700, False, protocol_overhead_tokens=0), soft_limit=0.4, recent_group_count=1).prepare_for_llm(
        session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=5,
        messages=[ChatMessage("system", "system"), ChatMessage("user", "inspect"), *dynamic], base_message_count=2, task="inspect", evidence={},
    )
    snapshots = ContextAuditRepository(database).list_for_turn(turn.turn_id)

    assert SCHEMA_VERSION == 8
    assert len(snapshots) == 1
    assert snapshots[0].scope == "turn"
    assert snapshots[0].checkpoint_id is not None
    assert all(len(item.preview) <= 200 for item in snapshots[0].message_manifest)
    assert "secret-value" not in str(snapshots[0].redacted_preview)


def test_turn_snapshot_preserves_selected_memory_instruction_and_summary_sources(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="tiny", permission_mode="manual")
    turn = store.create_turn(session_id=session.session_id, title="sources", provider_snapshot="openai", model_snapshot="tiny", permission_mode_snapshot="manual", branch_snapshot=None)
    user = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="inspect")
    summary = store.create_context_summary(session_id=session.session_id, turn_id=turn.turn_id, content="summary")
    base_messages = [
        ChatMessage("system", "system"),
        ChatMessage("system", "instructions"),
        ChatMessage("system", "memory"),
        ChatMessage("system", "summary"),
        ChatMessage("user", "inspect"),
    ]
    selected = (
        _item("system-0", base_messages[0], "system_prompt"),
        _item("project-instructions", base_messages[1], "instruction", "instruction-1"),
        _item("project-memory", base_messages[2], "memory", "memory-1"),
        _item(f"summary-{summary.summary_id}", base_messages[3], "summary", summary.summary_id),
        _item(f"message-{user.message_id}", base_messages[4], "message", user.message_id),
    )
    dynamic = []
    for index in range(4):
        assistant = ChatMessage("assistant", f"call-{index}")
        observation = ChatMessage("user", "Tool: read_file\n" + "x" * 1000)
        dynamic.extend((assistant, observation))
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content=assistant.content)
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content=observation.content)

    TurnContextWindow(database, ModelContextProfile("openai", "tiny", 1000, False, protocol_overhead_tokens=0), soft_limit=0.5, recent_group_count=1).prepare_for_llm(
        session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=5,
        messages=[*base_messages, *dynamic], base_message_count=len(base_messages), task="inspect", evidence={},
        selected_context_items=selected, omitted_context_items=(_item("omitted-history", ChatMessage("user", "old"), "message", "old-message"),),
    )
    snapshot = ContextAuditRepository(database).list_for_turn(turn.turn_id)[0]

    assert snapshot.instruction_ids == ("instruction-1",)
    assert snapshot.memory_ids == ("memory-1",)
    assert snapshot.summary_id == summary.summary_id
    assert snapshot.checkpoint_id is not None
    assert "project-instructions" in snapshot.selected_context_keys
    assert "omitted-history" in snapshot.omitted_context_keys


def _item(key: str, message: ChatMessage, source_kind: str, source_id: str | None = None) -> ContextItem:
    return ContextItem(key, (message,), estimate_tokens(message), True, 500, source_kind=source_kind, source_ids=(source_id,) if source_id else ())
