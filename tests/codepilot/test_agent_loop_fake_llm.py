from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.trace.logger import TraceLogger


def write_bug_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def _read_trace_events(trace_path: Path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


def _build_loop(tmp_path: Path, responses: list[str], *, mode: str = "build", approve: bool = True, max_steps: int = 12) -> MinimalAgentLoop:
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="run-test",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode=mode, approved=approve, interactive=False),
    )
    return MinimalAgentLoop(llm=FakeLLMClient(responses), router=router, max_steps=max_steps)


def test_loop_rejects_different_trace_logger(tmp_path: Path) -> None:
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs-a", run_id="a")
    other_logger = TraceLogger(runs_dir=tmp_path / "runs-b", run_id="b")

    with pytest.raises(ValueError, match="same TraceLogger"):
        MinimalAgentLoop(
            llm=FakeLLMClient([]),
            router=router,
            trace_logger=other_logger,
        )


def test_fake_loop_success(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"tool_call","tool_name":"run_tests","arguments":{"command":"python -m pytest -q","timeout":30}}',
            '{"type":"tool_call","tool_name":"git_status","arguments":{}}',
            '{"type":"tool_call","tool_name":"git_diff","arguments":{"path":"src/calc.py","include_content":true}}',
            '{"type":"finish","status":"success","summary":"Fixed add() and verified tests passed.","tests":"python -m pytest -q passed","changed_files":["src/calc.py"]}',
        ],
    )

    result = loop.run("Fix the failing add test", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is True
    assert result.status == "success"
    assert result.steps == 6
    assert "src/calc.py" in result.changed_files
    assert result.last_test_status == "passed"
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert {"run_start", "llm_call", "agent_action", "policy_decision", "tool_call", "agent_observation", "agent_finish", "run_end"} <= {
        event["event_type"] for event in events
    }
    replace_policy = next(event for event in events if event["event_type"] == "policy_decision" and event["tool_name"] == "replace_range")
    assert replace_policy["metadata"]["approved"] is True
    assert any(event["event_type"] == "tool_call" and event["tool_name"] == "run_tests" for event in events)


def test_fake_loop_recovers_after_invalid_json(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        ["not json", '{"type":"finish","status":"partial","summary":"Need another step"}'],
        max_steps=2,
    )

    result = loop.run("Inspect repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "partial"
    assert any(event["event_type"] == "agent_action" and event["success"] is False for event in events)


def test_fake_loop_read_only_denies_edit(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"finish","status":"partial","summary":"Edit denied by policy"}',
        ],
        mode="read_only",
        approve=False,
        max_steps=2,
    )

    result = loop.run("Fix repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is False
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert [event["event_type"] for event in events if event.get("tool_name") == "replace_range"] == [
        "agent_action",
        "policy_decision",
        "agent_observation",
    ]


def test_fake_loop_stops_at_max_steps(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
        ],
        max_steps=3,
    )

    result = loop.run("Inspect repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "max_steps_exceeded"
    assert events[-1]["event_type"] == "run_end"
    assert events[-1]["success"] is False


def test_fake_loop_unknown_tool_does_not_crash(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"unknown_tool","arguments":{}}',
            '{"type":"finish","status":"partial","summary":"Unknown tool handled"}',
        ],
        max_steps=2,
    )

    result = loop.run("Inspect repo", repo)

    assert result.status == "partial"


def test_fake_loop_blocks_finish_success_until_tests_pass(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"finish","status":"success","summary":"done"}',
            '{"type":"tool_call","tool_name":"run_tests","arguments":{"command":"pytest","timeout":30}}',
            '{"type":"finish","status":"success","summary":"done after passed tests","tests":"pytest passed","changed_files":["src/calc.py"]}',
        ],
        max_steps=4,
    )

    result = loop.run("Fix the failing add test", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is True
    assert result.status == "success"
    assert result.last_test_status == "passed"
    assert any(
        event["event_type"] == "agent_action"
        and event["metadata"].get("finish_blocked_without_passed_tests") is True
        for event in events
    )
    assert any(
        event["event_type"] == "agent_observation"
        and "Finish blocked." in (event.get("output_summary") or "")
        for event in events
    )


def test_fake_loop_allows_partial_finish_without_tests(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        ['{"type":"finish","status":"partial","summary":"Need more work"}'],
        max_steps=1,
    )

    result = loop.run("Inspect repo", repo)

    assert result.success is False
    assert result.status == "partial"
