from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.permission_broker import BlockingTUIBroker
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig
from codepilot.tui_agent.session_store import SessionStore


def _make_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    return tmp_path


def test_set_permission_mode_switches_broker_and_persists_session(tmp_path: Path) -> None:
    project = resolve_project(_make_repo(tmp_path))
    store = SessionStore(project, SessionDatabase(tmp_path / "data" / "sessions.sqlite3"))
    session = store.create_session(model=None, permission_mode="manual")
    event_stream = MemoryEventStream()
    runner = TUIAgentRunner(
        project=project,
        session=session,
        session_store=store,
        event_stream=event_stream,
        permission_broker=BlockingTUIBroker(),
        config=TUIRunnerConfig(
            model=None,
            model_config=(),
            permission_mode="manual",
            fake_actions=None,
            mcp_config=None,
            max_steps=1,
            auto_report=False,
        ),
    )

    runner.set_permission_mode("accept_edits")
    assert runner.config.permission_mode == "accept_edits"
    assert runner.permission_broker.inner.__class__.__name__ == "AutoApproveLocalWriteBroker"
    runner.set_permission_mode("read_only")
    assert runner.config.permission_mode == "read_only"
    assert runner.permission_broker.inner.__class__.__name__ == "BlockingTUIBroker"

    updated = store.update_session(session, permission_mode="read_only")
    assert updated.permission_mode == "read_only"
