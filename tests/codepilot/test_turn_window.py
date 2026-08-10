from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import ChatMessage, ChatMessagePart, LLMResponse, LLMToolCall, RichChatMessage
from codepilot.memory.repository import TurnCheckpointRepository
from codepilot.memory.turn_window import CHECKPOINT_PREFIX, TurnContextWindow
from codepilot.router import ToolRouter
from codepilot.session.context import ContextAssembler
from codepilot.session.context_budget import ContextBudgetExceeded, estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore


def _turn(tmp_path: Path):
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="tiny", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="rolling",
        provider_snapshot="openai",
        model_snapshot="tiny",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="fix the project")
    return database, store, session, turn


def _native_exchange(tool_name: str, provider_tool_call_id: str, arguments: dict, content: str) -> tuple[RichChatMessage, RichChatMessage]:
    return (
        RichChatMessage(
            role="assistant",
            parts=(
                ChatMessagePart(
                    type="tool_call",
                    content={
                        "provider_tool_call_id": provider_tool_call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                    },
                ),
            ),
        ),
        RichChatMessage(
            role="tool",
            parts=(
                ChatMessagePart(
                    type="tool_result",
                    content={
                        "provider_tool_call_id": provider_tool_call_id,
                        "tool_name": tool_name,
                        "content": content,
                    },
                ),
            ),
        ),
    )


def _store_native_exchange(store: SessionStore, session_id: str, turn_id: str, exchange: tuple[RichChatMessage, RichChatMessage]) -> None:
    assistant, tool = exchange
    assistant_record = store.create_message(session_id=session_id, turn_id=turn_id, role="assistant", status="completed", content="")
    store.append_message_part(assistant_record.message_id, type="tool_call", content=assistant.parts[0].content)
    tool_record = store.create_message(session_id=session_id, turn_id=turn_id, role="tool", status="completed", content=tool.parts[0].content["content"])
    store.append_message_part(tool_record.message_id, type="tool_result", content=tool.parts[0].content)


def test_turn_window_persists_checkpoint_and_keeps_raw_messages(tmp_path: Path) -> None:
    database, store, session, turn = _turn(tmp_path)
    dynamic = []
    for index in range(5):
        exchange = _native_exchange("read_file", f"provider-{index}", {"path": f"{index}.py"}, "x" * 500)
        dynamic.extend(exchange)
        _store_native_exchange(store, session.session_id, turn.turn_id, exchange)
    messages = [ChatMessage("system", "system"), ChatMessage("user", "fix the project"), *dynamic]
    profile = ModelContextProfile("openai", "tiny", 500, False, protocol_overhead_tokens=0)

    prepared, base_count = TurnContextWindow(database, profile, soft_limit=0.4, recent_group_count=2).prepare_for_llm(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=None,
        step=6,
        messages=messages,
        base_message_count=2,
        task="fix the project",
        evidence={"last_test_status": "passed"},
    )
    checkpoint = TurnCheckpointRepository(database).latest(turn.turn_id)

    assert checkpoint is not None
    assert checkpoint.covered_message_ids
    assert len(store.list_messages_with_parts(session.session_id)) == 11
    assert any(message.role == "system" and message.content.startswith(CHECKPOINT_PREFIX) for message in prepared)
    assert sum(estimate_tokens(message) for message in prepared) <= profile.max_input_tokens
    assert base_count == 3

    next_assistant, next_observation = _native_exchange("git_diff", "provider-next", {}, "y" * 500)
    _store_native_exchange(store, session.session_id, turn.turn_id, (next_assistant, next_observation))
    prepared, base_count = TurnContextWindow(database, profile, soft_limit=0.4, recent_group_count=2).prepare_for_llm(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=None,
        step=7,
        messages=[*prepared, next_assistant, next_observation],
        base_message_count=base_count,
        task="fix the project",
        evidence={"diff_checked": True},
    )
    replacement = TurnCheckpointRepository(database).latest(turn.turn_id)

    assert replacement is not None
    assert replacement.checkpoint_id != checkpoint.checkpoint_id
    assert set(checkpoint.covered_message_ids) <= set(replacement.covered_message_ids)
    with database.transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM turn_memory_checkpoints WHERE turn_id = ? AND status = 'active'",
            (turn.turn_id,),
        ).fetchone()[0] == 1

    plan = ContextAssembler(database).build_plan(session.session_id, turn.turn_id, "openai", "tiny", profile=profile)

    assert next(item for item in plan.summary_items if item.key.startswith("turn-checkpoint-")).mandatory is True
    assert not {f"message-{message_id}" for message_id in replacement.covered_message_ids} & {
        item.key for item in plan.current_turn_items
    }
    recovery_profile = ModelContextProfile("openai", "tiny", 16_384, False, protocol_overhead_tokens=0)
    recovered = ContextAssembler(database).build(
        session.session_id,
        turn.turn_id,
        "openai",
        "tiny",
        profile=recovery_profile,
    )

    _, recovered_base_count = TurnContextWindow(database, recovery_profile, soft_limit=1).prepare_for_llm(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=None,
        step=7,
        messages=recovered,
        base_message_count=len(recovered),
        task="fix the project",
        evidence={},
    )

    assert recovered_base_count < len(recovered)


class _RecordingWindow:
    def __init__(self) -> None:
        self.steps = []

    def prepare_for_llm(self, **values):
        self.steps.append(values["step"])
        return values["messages"], values["base_message_count"]


def test_agent_loop_checks_context_before_every_llm_call(tmp_path: Path) -> None:
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs", run_id="rolling")
    window = _RecordingWindow()
    result = MinimalAgentLoop(
        llm=StructuredFakeLLM(
            [
                LLMResponse(content="", tool_calls=(LLMToolCall(provider_tool_call_id="provider-1", name="list_files", arguments={}),)),
                LLMResponse(content="", tool_calls=(LLMToolCall(provider_tool_call_id="provider-2", name="codepilot_finish", arguments={"status": "partial", "summary": "stop"}),)),
            ]
        ),
        router=router,
        max_steps=2,
        context_window=window,
    ).run("inspect", tmp_path)

    assert result.status == "partial"
    assert window.steps == [1, 2]


def test_turn_window_fits_checkpoint_with_large_pending_replacement(tmp_path: Path) -> None:
    database, store, session, turn = _turn(tmp_path)
    replacement = "new line\n" * 2500
    call = store.create_tool_call(
        turn_id=turn.turn_id,
        tool_name="replace_range",
        arguments={"path": "src/app.py", "start_line": 1, "end_line": 2, "replacement": replacement},
    )
    dynamic = []
    for index in range(4):
        exchange = _native_exchange("read_file", f"provider-{index}", {"path": f"{index}.py"}, "x" * 900)
        dynamic.extend(exchange)
        _store_native_exchange(store, session.session_id, turn.turn_id, exchange)
    profile = ModelContextProfile("openai", "tiny", 900, False, protocol_overhead_tokens=0)

    prepared, _ = TurnContextWindow(database, profile, soft_limit=0.5, recent_group_count=1).prepare_for_llm(
        session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=5,
        messages=[ChatMessage("system", "system"), ChatMessage("user", "fix"), *dynamic], base_message_count=2,
        task="fix", evidence={},
    )
    checkpoint = TurnCheckpointRepository(database).latest(turn.turn_id)

    assert checkpoint is not None
    assert replacement not in str(checkpoint.content)
    assert checkpoint.content["pending_tool_calls"][0]["arguments"]["replacement_chars"] == len(replacement)
    assert store.get_tool_call(call.tool_call_id).arguments["replacement"] == replacement
    assert sum(estimate_tokens(message) for message in prepared) <= profile.max_input_tokens


def test_turn_window_covers_oversized_latest_completed_tool_group(tmp_path: Path) -> None:
    database, store, session, turn = _turn(tmp_path)
    small_assistant, small_result = _native_exchange("read_file", "provider-small", {}, "small result")
    _store_native_exchange(store, session.session_id, turn.turn_id, (small_assistant, small_result))
    patch = "x" * 30_000
    assistant, result_message = _native_exchange("apply_patch", "provider-patch", {"path": "src/app.py", "patch": patch}, "changed")
    assistant_record = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content="")
    store.append_message_part(assistant_record.message_id, type="tool_call", content=assistant.parts[0].content)
    call = store.create_tool_call(turn_id=turn.turn_id, tool_name="apply_patch", arguments={"path": "src/app.py", "patch": patch}, message_id=assistant_record.message_id)
    store.persist_tool_result(call.tool_call_id, call_status="completed", result_status="success", content="changed", success=True, metadata={"changed": True, "changed_files": ["src/app.py"]})
    result_record = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content="changed", metadata={"tool_call_id": call.tool_call_id, "success": True})
    store.append_message_part(result_record.message_id, type="tool_result", content={**result_message.parts[0].content, "codepilot_tool_call_id": call.tool_call_id})
    profile = ModelContextProfile("openai", "tiny", 500, False, protocol_overhead_tokens=0)

    prepared = TurnContextWindow(database, profile, soft_limit=0.4, recent_group_count=2).prepare_for_llm(
        session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3,
        messages=[ChatMessage("system", "system"), ChatMessage("user", "fix"), small_assistant, small_result, assistant, result_message],
        base_message_count=2, task="fix", evidence={},
    )
    checkpoint = TurnCheckpointRepository(database).latest(turn.turn_id)

    assert checkpoint is not None
    assert result_record.message_id in checkpoint.covered_message_ids
    assert "src/app.py" in str(checkpoint.content)
    assert sum(estimate_tokens(message) for message in prepared.messages) <= profile.max_input_tokens
    assert store.get_tool_call(call.tool_call_id).arguments["patch"] == patch


def test_minimal_checkpoint_still_fails_when_mandatory_base_cannot_fit(tmp_path: Path) -> None:
    database, _, session, turn = _turn(tmp_path)
    profile = ModelContextProfile("openai", "tiny", 20, False, protocol_overhead_tokens=0)

    with pytest.raises(ContextBudgetExceeded):
        TurnContextWindow(database, profile, soft_limit=0.1).prepare_for_llm(
            session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=1,
            messages=[ChatMessage("system", "x" * 200), ChatMessage("user", "fix")], base_message_count=2,
            task="fix", evidence={},
        )
