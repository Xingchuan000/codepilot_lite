from __future__ import annotations

from pathlib import Path

from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import LLMResponse
from codepilot.multi_agent.profiles import EXPLORE_PROFILE
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.session.database import SessionDatabase
from codepilot.session.models import TurnSubmission
from codepilot.session.runtime import SessionRuntime
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore


def test_child_runtime_does_not_extract_memory_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    service = SessionService(database)
    store = SessionStore(database)
    parent = service.create_session(repo, "fake", "fake", "manual")
    parent_turn = store.create_turn(
        session_id=parent.session_id,
        title="primary",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    child = service.create_child_session(
        parent_session_id=parent.session_id,
        forked_from_turn_id=parent_turn.turn_id,
        provider="fake",
        model="fake",
        permission_mode="manual",
        metadata={"agent_type": "explore", "memory_write": False},
    )

    runtime = SessionRuntime(
        database,
        StructuredFakeLLM([LLMResponse(content="read-only result")]),
        lambda trace: ToolRouter(
            trace,
            policy_checker=PolicyChecker.default(),
            policy_context=PolicyContext(repo=repo, mode="read_only", interactive=False),
        ),
        agent_profile=EXPLORE_PROFILE,
    )
    submission = runtime.submit_user_message(child.session_id, "inspect")
    assert isinstance(submission, TurnSubmission)

    execution = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    assert execution.result.status == "message_complete"
    assert all(event.event_type != "memory_candidates_extracted" for event in store.list_events(child.session_id))
