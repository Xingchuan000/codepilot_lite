from __future__ import annotations

import subprocess
import time
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.permission_broker import NonInteractiveBroker
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig
from codepilot.tui_agent.session_store import SessionStore


def test_runner_setup_failure_is_recorded_in_sqlite(tmp_path: Path, monkeypatch) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    project = resolve_project(tmp_path)
    store = SessionStore(project, SessionDatabase(tmp_path / "data" / "sessions.sqlite3"))
    session = store.create_session(model=None, permission_mode="unsafe_auto")
    events = MemoryEventStream()
    runner = TUIAgentRunner(
        project=project,
        session=session,
        session_store=store,
        event_stream=events,
        permission_broker=NonInteractiveBroker(),
        config=TUIRunnerConfig(model=None, model_config=(), permission_mode="unsafe_auto", fake_actions=None, mcp_config=None, max_steps=1, auto_report=False),
    )
    monkeypatch.setattr("codepilot.tui_agent.runner.build_codepilot_llm", lambda **_: (_ for _ in ()).throw(RuntimeError("model config invalid")))

    runner.start_task("fix add")
    deadline = time.time() + 20
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.05)

    assert runner.is_running() is False
    assert store.service.store.list_turns(session.session_id) == []
    assert events.drain()
