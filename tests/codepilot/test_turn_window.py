from __future__ import annotations

from pathlib import Path

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.llm.types import ChatMessage
from codepilot.memory.repository import TurnCheckpointRepository
from codepilot.memory.turn_window import CHECKPOINT_PREFIX, TurnContextWindow
from codepilot.router import ToolRouter
from codepilot.session.context import ContextAssembler
from codepilot.session.context_budget import estimate_tokens
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


def test_turn_window_persists_checkpoint_and_keeps_raw_messages(tmp_path: Path) -> None:
    database, store, session, turn = _turn(tmp_path)
    dynamic = []
    for index in range(5):
        assistant = ChatMessage("assistant", f'{{"type":"tool_call","tool_name":"read_file","arguments":{{"path":"{index}.py"}}}}')
        observation = ChatMessage("user", f"Tool: read_file\nSuccess: true\nOutput preview:\n{'x' * 500}")
        dynamic.extend((assistant, observation))
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content=assistant.content)
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content=observation.content)
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

    next_assistant = ChatMessage("assistant", '{"type":"tool_call","tool_name":"git_diff","arguments":{}}')
    next_observation = ChatMessage("user", f"Tool: git_diff\nSuccess: true\nOutput preview:\n{'y' * 500}")
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content=next_assistant.content)
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content=next_observation.content)
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
        llm=FakeLLMClient(['{"type":"tool_call"', '{"type":"finish","status":"partial","summary":"stop"}']),
        router=router,
        max_steps=2,
        context_window=window,
    ).run("inspect", tmp_path)

    assert result.status == "partial"
    assert window.steps == [1, 2]
