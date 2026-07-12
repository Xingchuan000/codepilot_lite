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


def _event_types(events: list[dict]) -> list[str]:
    return [event["event_type"] for event in events]


def test_loop_rejects_different_trace_logger(tmp_path: Path) -> None:
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs-a", run_id="a")
    other_logger = TraceLogger(runs_dir=tmp_path / "runs-b", run_id="b")

    with pytest.raises(ValueError, match="same TraceLogger"):
        MinimalAgentLoop(
            llm=FakeLLMClient([]),
            router=router,
            trace_logger=other_logger,
        )


def test_fake_loop_greeting_becomes_message_complete(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(tmp_path, ["Hello there"]).run("hello", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is True
    assert result.status == "message_complete"
    assert result.completion_kind == "message_complete"
    assert result.assistant_stop_reason == "natural_reply"
    assert result.changed_files == []
    assert "tool_call" not in _event_types(events)
    assert not any(event["event_type"] == "agent_action" and event.get("success") is False for event in events)
    assert not any(event["event_type"] == "agent_observation" and "Finish blocked." in (event.get("output_summary") or "") for event in events)


def test_fake_loop_natural_reply_without_write_is_message_complete(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(tmp_path, ["已经修复"]).run("修复 add bug", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is True
    assert result.status == "message_complete"
    assert result.completion_kind == "message_complete"
    assert result.assistant_stop_reason == "natural_reply"
    assert not any(event["event_type"] == "tool_call" for event in events)


def test_fake_loop_non_code_finish_success_becomes_message_complete(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(tmp_path, ['{"type":"finish","status":"success","summary":"done"}']).run("hello", repo)

    assert result.success is True
    assert result.status == "message_complete"
    assert result.completion_kind == "message_complete"


def test_fake_loop_natural_reply_after_write_cannot_be_task_success(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            "已经修复",
        ],
        max_steps=2,
    ).run("修复 add bug", repo)

    assert result.success is False
    assert result.status == "task_incomplete"
    assert result.completion_kind == "task_incomplete"
    assert result.assistant_stop_reason == "natural_reply"


def test_finish_claiming_changed_files_without_write_is_blocked(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"finish","status":"success","summary":"done","changed_files":["src/calc.py"]}',
            '{"type":"finish","status":"partial","summary":"Need more work"}',
        ],
        max_steps=2,
    ).run("修复 add bug", repo)

    assert result.success is False
    assert result.status == "partial"
    assert result.delivery_kind == "code_change"
    assert result.requires_evidence is True
    assert result.changed_files == []
    assert result.claimed_changed_files == ["src/calc.py"]
    assert result.written_files == []
    assert "missing_write_execution" in result.missing_evidence
    assert "missing_changed_files" in result.missing_evidence
    assert result.tests_required is False
    assert result.diff_required is False


def test_finish_explicit_message_with_changed_files_is_still_code_change(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        ['{"type":"finish","status":"success","delivery_kind":"message","summary":"done","changed_files":["src/calc.py"]}', '{"type":"finish","status":"partial","summary":"Need more work"}'],
        max_steps=2,
    ).run("修复 add bug", repo)

    assert result.delivery_kind == "code_change"
    assert result.status == "partial"
    assert result.changed_files == []


def test_fake_loop_missing_tests_is_blocked(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"finish","status":"success","summary":"done"}',
            '{"type":"finish","status":"partial","summary":"Need more work"}',
        ],
        max_steps=3,
    ).run("修复 add bug", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "partial"
    assert any("missing_passed_tests" in json.dumps(event) for event in events if event["event_type"] == "agent_observation")


def test_fake_loop_missing_diff_is_blocked(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"tool_call","tool_name":"run_tests","arguments":{"command":"pytest","timeout":30}}',
            '{"type":"finish","status":"success","summary":"done"}',
            '{"type":"finish","status":"partial","summary":"Need more work"}',
        ],
        max_steps=4,
    ).run("修复 add bug", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "partial"
    assert result.last_test_status == "passed"
    assert any("missing_diff_check" in json.dumps(event) for event in events if event["event_type"] == "agent_observation")


def test_fake_loop_success_requires_write_test_and_diff(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"tool_call","tool_name":"run_tests","arguments":{"command":"python -m pytest -q","timeout":30}}',
            '{"type":"tool_call","tool_name":"git_diff","arguments":{"path":"src/calc.py","include_content":true}}',
            '{"type":"finish","status":"success","summary":"Fixed add() and verified tests passed.","tests":"python -m pytest -q passed","changed_files":["src/calc.py"]}',
        ],
        max_steps=4,
    ).run("修复 add bug", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is True
    assert result.status == "success"
    assert result.completion_kind == "task_success"
    assert result.delivery_kind == "code_change"
    assert result.last_test_status == "passed"
    assert result.diff_checked is True
    assert result.written_files == ["src/calc.py"]
    assert result.claimed_changed_files == ["src/calc.py"]
    assert any(event["event_type"] == "agent_finish" and event["metadata"].get("completion_kind") == "task_success" for event in events)


def test_fake_loop_git_status_only_still_finishes_as_message_complete(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"git_status","arguments":{}}',
            '{"type":"finish","status":"success","summary":"done","changed_files":["src/calc.py"]}',
            '{"type":"finish","status":"partial","summary":"Need more work"}',
        ],
        max_steps=3,
    ).run("修复 add bug", repo)

    assert result.success is False
    assert result.status == "partial"
    assert result.completion_kind == "task_partial"
    assert result.delivery_kind == "code_change"
    assert not result.written_files
    assert result.claimed_changed_files == ["src/calc.py"]


def test_fake_loop_denied_write_attempt_is_blocked(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"replace_range","arguments":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"type":"finish","status":"success","summary":"done"}',
            '{"type":"finish","status":"partial","summary":"Edit denied by policy"}',
        ],
        mode="read_only",
        approve=False,
        max_steps=3,
    ).run("Fix repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "partial"
    assert any(event["event_type"] == "policy_decision" and event["tool_name"] == "replace_range" and event["policy_decision"] == "deny" for event in events)


def test_fake_loop_partial_finish_can_end_run(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(tmp_path, ['{"type":"finish","status":"partial","summary":"Need more work"}'], max_steps=1).run("Inspect repo", repo)

    assert result.success is False
    assert result.status == "partial"
    assert result.completion_kind == "task_partial"
    events = _read_trace_events(Path(result.trace_path))
    assert any(event["event_type"] == "agent_action" and event["success"] is True and event["metadata"].get("completion_kind") == "task_partial" for event in events)


def test_fake_loop_failed_finish_can_end_run(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(tmp_path, ['{"type":"finish","status":"failed","summary":"Not fixed"}'], max_steps=1).run("Inspect repo", repo)

    assert result.success is False
    assert result.status == "failed"
    assert result.completion_kind == "task_failed"


def test_fake_loop_recovers_after_malformed_json(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        ['{"type":"tool_call"', '{"type":"finish","status":"partial","summary":"Need another step"}'],
        max_steps=2,
    ).run("Inspect repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "partial"
    assert any(event["event_type"] == "agent_action" and event["success"] is False and event["metadata"].get("parse_success") is False for event in events)


def test_fake_loop_recovers_after_multiple_json_objects(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        ['{"type":"finish","status":"partial","summary":"one"}{"type":"finish","status":"partial","summary":"two"}', '{"type":"finish","status":"partial","summary":"Need another step"}'],
        max_steps=2,
    ).run("Inspect repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "partial"
    assert any(event["event_type"] == "agent_action" and event["success"] is False and event["metadata"].get("parse_success") is False for event in events)


def test_fake_loop_stops_at_max_steps(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
            '{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}',
        ],
        max_steps=3,
    ).run("Inspect repo", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.status == "max_steps_exceeded"
    assert events[-1]["event_type"] == "run_end"
    assert events[-1]["success"] is False
