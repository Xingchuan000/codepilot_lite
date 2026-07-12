from __future__ import annotations

import subprocess
import time
from pathlib import Path

from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.permission_broker import BlockingTUIBroker, NonInteractiveBroker
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig
from codepilot.tui_agent.session_store import SessionStore
from codepilot.tui_agent.models import TUISessionRunRef


def _make_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    return tmp_path


def _wait_for_runner(runner: TUIAgentRunner) -> None:
    deadline = time.time() + 20
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.05)


def test_set_permission_mode_switches_broker_and_persists_session(tmp_path: Path) -> None:
    project = resolve_project(_make_repo(tmp_path))
    store = SessionStore(project)
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
    assert runner.permission_broker.__class__.__name__ == "AutoApproveLocalWriteBroker"
    runner.set_permission_mode("read_only")
    assert runner.config.permission_mode == "read_only"
    assert runner.permission_broker.__class__.__name__ == "BlockingTUIBroker"

    updated = store.update_session(session, permission_mode="read_only")
    assert updated.permission_mode == "read_only"


def test_cancel_current_does_not_poison_next_task(tmp_path: Path) -> None:
    project = resolve_project(_make_repo(tmp_path))
    store = SessionStore(project)
    session = store.create_session(model=None, permission_mode="unsafe_auto")
    event_stream = MemoryEventStream()
    runner = TUIAgentRunner(
        project=project,
        session=session,
        session_store=store,
        event_stream=event_stream,
        permission_broker=NonInteractiveBroker(),
        config=TUIRunnerConfig(
            model=None,
            model_config=(),
            permission_mode="unsafe_auto",
            fake_actions=Path("tests/codepilot/fixtures/tui_agent_actions_success.jsonl"),
            mcp_config=None,
            max_steps=12,
            auto_report=False,
        ),
    )

    runner.cancel_current()
    runner.start_task("fix add")
    assert runner.cancellation_token.is_cancelled() is False
    _wait_for_runner(runner)

    assert runner.session.last_run_id is not None
    assert (runner.session.session_dir / "session.json").exists()
    assert any(event.type == "run_started" for event in event_stream.drain())


def test_cancel_current_wakes_pending_permission(tmp_path: Path) -> None:
    project = resolve_project(_make_repo(tmp_path))
    store = SessionStore(project)
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
            fake_actions=Path("tests/codepilot/fixtures/tui_agent_actions_success.jsonl"),
            mcp_config=None,
            max_steps=12,
            auto_report=False,
        ),
    )

    runner.start_task("fix add")
    deadline = time.time() + 20
    while time.time() < deadline:
        if any(event.type == "permission_requested" for event in event_stream.drain()):
            break
        time.sleep(0.05)

    runner.cancel_current()
    _wait_for_runner(runner)

    assert runner.session.runs[-1].status == "cancelled"
    assert runner.cancellation_token.is_cancelled() is True


def test_session_round_trip_preserves_validation_state(tmp_path: Path) -> None:
    project = resolve_project(_make_repo(tmp_path))
    store = SessionStore(project)
    session = store.create_session(model=None, permission_mode="manual")

    updated = store.append_run(
        session,
        TUISessionRunRef(
            run_id="run-1",
            task_preview="fix add",
            status="success",
            completion_kind="task_success",
            assistant_stop_reason="structured_finish",
            delivery_kind="code_change",
            requires_evidence=True,
            evidence_reasons=("task_requires_code_delivery",),
            write_attempted=True,
            write_executed=True,
            written_files=("src/calc.py",),
            changed_files=("src/calc.py",),
            tests_required=True,
            diff_required=True,
            diff_checked=True,
            missing_evidence=("missing_diff_check",),
            tests="passed",
        ),
    )
    loaded = store.load_session(updated.session_id)

    assert loaded.runs[0].delivery_kind == "code_change"
    assert loaded.runs[0].tests_required is True
    assert loaded.runs[0].diff_required is True
    assert loaded.runs[0].diff_checked is True
    assert loaded.runs[0].missing_evidence == ("missing_diff_check",)
