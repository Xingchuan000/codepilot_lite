from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.permission_broker import NonInteractiveBroker
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


def _make_runner(tmp_path: Path, *, fake_actions: str | Path | None, auto_report: bool = True) -> tuple[TUIAgentRunner, MemoryEventStream]:
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
            fake_actions=fake_actions,
            mcp_config=None,
            max_steps=12,
            auto_report=auto_report,
        ),
    )
    return runner, event_stream


def _wait_for_runner(runner: TUIAgentRunner) -> None:
    deadline = time.time() + 20
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.05)


def _trace_events(runner: TUIAgentRunner, run_id: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (runner.session.runs_dir / run_id / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


def test_runner_setup_failure_emits_failed_finish_and_clears_thread_state(tmp_path: Path, monkeypatch) -> None:
    runner, event_stream = _make_runner(tmp_path, fake_actions=None, auto_report=False)

    def broken_builder(**kwargs):  # noqa: ANN001
        raise RuntimeError("model config invalid")

    monkeypatch.setattr("codepilot.tui_agent.runner.build_codepilot_llm", broken_builder)

    runner.start_task("fix add")
    _wait_for_runner(runner)

    events = event_stream.drain()
    finished = [event for event in events if event.type == "run_finished"]

    assert runner.is_running() is False
    assert runner._current_run_id is None
    assert len(finished) == 1
    assert finished[0].payload["status"] == "failed"
    assert finished[0].payload["success"] is False
    assert finished[0].payload["failure_source"] == "runner_setup"
    assert finished[0].payload["completion_kind"] == "runtime_failure"
    assert finished[0].payload["assistant_stop_reason"] is None


def test_runner_runtime_failure_emits_failed_finish_and_records_one_terminal_event(tmp_path: Path, monkeypatch) -> None:
    runner, event_stream = _make_runner(tmp_path, fake_actions=Path("tests/codepilot/fixtures/tui_agent_actions_success.jsonl"), auto_report=False)

    def broken_run(self, *, task: str, repo: Path):  # noqa: ANN001
        raise RuntimeError("unexpected loop crash")

    monkeypatch.setattr("codepilot.agent.loop.MinimalAgentLoop.run", broken_run)

    run_id = runner.start_task("fix add")
    _wait_for_runner(runner)

    events = event_stream.drain()
    finished = [event for event in events if event.type == "run_finished"]
    diagnostics = [event for event in events if event.type == "error"]
    trace_events = _trace_events(runner, run_id)

    assert runner.is_running() is False
    assert runner.session.runs[-1].status == "failed"
    assert len(finished) == 1
    assert finished[0].payload["status"] == "failed"
    assert finished[0].payload["failure_source"] == "agent_runtime"
    assert diagnostics[-1].payload["source"] == "agent_runtime"
    assert diagnostics[-1].payload["fatal"] is True
    assert sum(1 for item in trace_events if item["event_type"] in {"run_end", "run_cancelled"}) == 1


def test_runner_report_failure_keeps_agent_result_and_emits_warning(tmp_path: Path, monkeypatch) -> None:
    runner, event_stream = _make_runner(tmp_path, fake_actions=Path("tests/codepilot/fixtures/tui_agent_actions_success.jsonl"), auto_report=True)

    def broken_report(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("report broken")

    monkeypatch.setattr("codepilot.tui_agent.runner.generate_report", broken_report)

    run_id = runner.start_task("fix add")
    _wait_for_runner(runner)

    events = event_stream.drain()
    finished = [event for event in events if event.type == "run_finished"]
    diagnostics = [event for event in events if event.type == "error"]
    trace_events = _trace_events(runner, run_id)

    assert runner.is_running() is False
    assert runner.session.runs[-1].status == "success"
    assert len(finished) == 1
    assert finished[0].payload["status"] == "success"
    assert finished[0].payload["success"] is True
    assert finished[0].payload["report_path"] is None
    assert finished[0].payload["report_json_path"] is None
    assert diagnostics[-1].payload["source"] == "report_generation"
    assert diagnostics[-1].payload["fatal"] is False
    assert sum(1 for item in trace_events if item["event_type"] in {"run_end", "run_cancelled"}) == 1


def test_runner_session_persistence_failure_keeps_agent_result_and_reuses_session_object(tmp_path: Path, monkeypatch) -> None:
    runner, event_stream = _make_runner(tmp_path, fake_actions=Path("tests/codepilot/fixtures/tui_agent_actions_success.jsonl"), auto_report=False)
    session_before = runner.session
    calls: list[object] = []

    def broken_append_run(self, session, run_ref):  # noqa: ANN001
        calls.append(run_ref)
        raise RuntimeError("session store failed")

    monkeypatch.setattr("codepilot.tui_agent.session_store.SessionStore.append_run", broken_append_run)

    runner.start_task("fix add")
    _wait_for_runner(runner)

    events = event_stream.drain()
    finished = [event for event in events if event.type == "run_finished"]
    diagnostics = [event for event in events if event.type == "error"]

    assert runner.is_running() is False
    assert runner.session is session_before
    assert len(calls) == 1
    assert len(finished) == 1
    assert finished[0].payload["status"] == "success"
    assert finished[0].payload["success"] is True
    assert diagnostics[-1].payload["source"] == "session_persistence"
    assert diagnostics[-1].payload["fatal"] is False
