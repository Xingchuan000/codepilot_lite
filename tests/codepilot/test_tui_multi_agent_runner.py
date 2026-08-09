from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.permissions import PermissionResponse
from codepilot.session.database import SessionDatabase
from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.models import TUIEvent
from codepilot.tui_agent.permission_broker import BlockingTUIBroker
from codepilot.multi_agent.profiles import EXPLORE_PROFILE, SCOUT_PROFILE
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig, _policy_context_for_agent
from codepilot.tui_agent.session_controller import SessionController


def _runner(tmp_path: Path) -> tuple[TUIAgentRunner, SessionController, str]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    project = resolve_project(tmp_path)
    controller = SessionController(project, SessionDatabase(tmp_path / "data" / "sessions.sqlite3"))
    session = controller.create_session(model="fake", provider="fake", permission_mode="manual")
    runner = TUIAgentRunner(
        project=project,
        session=session,
        session_controller=controller,
        event_stream=MemoryEventStream(),
        permission_broker=BlockingTUIBroker(),
        config=TUIRunnerConfig(
            model=None,
            model_config=(),
            permission_mode="manual",
            fake_actions=Path("tests/codepilot/fixtures/agent_actions_success.jsonl"),
            mcp_config=None,
            max_steps=1,
        ),
    )
    return runner, controller, session.session_id


def test_runtime_factory_gives_primary_controls_but_not_child_controls(tmp_path: Path) -> None:
    runner, controller, parent_id = _runner(tmp_path)
    primary = runner._build_runtime_for_session(parent_id, supervisor=runner.agent_supervisor)
    assert primary.agent_profile is None
    assert primary.runtime_tool_registry_factory is not None

    parent_turn = controller.store.create_turn(
        session_id=parent_id,
        title="delegate",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    child = controller.service.create_child_session(
        parent_session_id=parent_id,
        forked_from_turn_id=parent_turn.turn_id,
        provider="fake",
        model="fake",
        permission_mode="manual",
        metadata={"agent_type": "explore", "memory_write": False},
    )

    child_runtime = runner._build_runtime_for_session(child.session_id, supervisor=None)
    assert child_runtime.agent_profile.name == "explore"
    assert child_runtime.runtime_tool_registry_factory is None


def test_child_permission_resolution_uses_request_session_id(tmp_path: Path) -> None:
    runner, controller, parent_id = _runner(tmp_path)
    parent_turn = controller.store.create_turn(
        session_id=parent_id,
        title="delegate",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    child = controller.service.create_child_session(
        parent_session_id=parent_id,
        forked_from_turn_id=parent_turn.turn_id,
        provider="fake",
        model="fake",
        permission_mode="manual",
        metadata={"agent_type": "general", "write_scope": ["README.md"]},
    )
    controller.store.create_permission_request(
        request_id="perm-child",
        session_id=child.session_id,
        turn_id=None,
        attempt_id=None,
        tool_call_id=None,
        scope_key='{"tool":"replace_range"}',
        tool_name="replace_range",
        arguments={"path": "README.md"},
        reason="child write",
        status="pending",
    )

    runner.resolve_permission(
        PermissionResponse(
            request_id="perm-child",
            decision="approve_session",
            reason="approved for child",
            responded_at="2026-08-09T00:00:00+00:00",
        )
    )

    assert controller.store.get_permission_request("perm-child").status == "approved"
    assert controller.store.get_permission_response_by_request("perm-child").decision == "approve_session"
    assert controller.store.get_permission_grant(child.session_id, '{"tool":"replace_range"}') is not None


def test_agent_lifecycle_events_are_rendered_as_status_only() -> None:
    reducer = EventReducer()
    view = reducer.reduce(
        TUIEvent(
            type="agent_patch_ready",
            timestamp="2026-08-09T00:00:00+00:00",
            session_id="parent",
            payload={
                "type": "agent_patch_ready",
                "agent_type": "general",
                "child_session_id": "child-1",
                "changed_files": ["README.md", "src/app.py"],
            },
        )
    )

    assert view.transcript[-1].kind == "system_status"
    assert view.transcript[-1].body == "[agent] general child-1 patch ready: 2 files"


def test_scout_keeps_network_capable_policy_when_parent_is_read_only(tmp_path: Path) -> None:
    scout = _policy_context_for_agent("read_only", tmp_path, SCOUT_PROFILE)
    explore = _policy_context_for_agent("manual", tmp_path, EXPLORE_PROFILE)

    assert scout.mode == "build"
    assert scout.approved is False
    assert explore.mode == "read_only"


def test_abort_child_permission_routes_by_request_session_id(tmp_path: Path) -> None:
    runner, controller, parent_id = _runner(tmp_path)
    parent_turn = controller.store.create_turn(
        session_id=parent_id,
        title="delegate",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    child = controller.service.create_child_session(
        parent_session_id=parent_id,
        forked_from_turn_id=parent_turn.turn_id,
        provider="fake",
        model="fake",
        permission_mode="manual",
        metadata={"agent_type": "general", "write_scope": ["README.md"]},
    )
    controller.store.create_permission_request(
        request_id="perm-child-abort",
        session_id=child.session_id,
        turn_id=None,
        attempt_id=None,
        tool_call_id=None,
        scope_key='{"tool":"replace_range"}',
        tool_name="replace_range",
        arguments={"path": "README.md"},
        reason="child write",
        status="pending",
    )

    runner.abort_pending_permission("perm-child-abort")

    request = controller.store.get_permission_request("perm-child-abort")
    response = controller.store.get_permission_response_by_request("perm-child-abort")
    assert request.status == "denied"
    assert response is not None
    assert response.decision == "deny"
