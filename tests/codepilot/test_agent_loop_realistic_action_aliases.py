from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter


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


def _build_loop(
    tmp_path: Path,
    responses: list[str],
    *,
    mode: str = "build",
    approve: bool = True,
    max_steps: int = 12,
) -> MinimalAgentLoop:
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="run-test",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode=mode, approved=approve, interactive=False),
    )
    return MinimalAgentLoop(llm=FakeLLMClient(responses), router=router, max_steps=max_steps)


def test_fake_loop_accepts_realistic_action_aliases(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"action":"read_file","parameters":{"path":"src/calc.py","start_line":1,"end_line":20}}',
            '{"type":"tool_call","tool":"replace_range","parameters":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
            '{"name":"run_tests","input":{"command":"python -m pytest -q","timeout":30}}',
            '{"type":"tool_call","tool":"git_status","parameters":{}}',
            '{"type":"tool_call","tool":"git_diff","parameters":{"path":"src/calc.py","include_content":true}}',
            '{"type":"finish","status":"success","summary":"Fixed add() and verified tests passed.","tests":"python -m pytest -q passed","changed_files":["src/calc.py"]}',
        ],
    )

    result = loop.run("Fix the failing add test", repo)
    events = _read_trace_events(Path(result.trace_path))

    assert result.success is True
    assert result.status == "success"
    assert result.steps == 6
    assert result.last_test_status == "passed"
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    normalized_events = [
        event for event in events if event["event_type"] == "agent_action" and event["metadata"].get("normalization_applied") is True
    ]
    assert len(normalized_events) >= 5
    assert any(event["tool_name"] == "read_file" for event in normalized_events)
    assert any(event["tool_name"] == "replace_range" for event in normalized_events)
    assert any(event["tool_name"] == "run_tests" for event in normalized_events)
    assert any(event["event_type"] == "policy_decision" and event["tool_name"] == "replace_range" for event in events)
    assert any(event["event_type"] == "tool_call" and event["tool_name"] == "run_tests" for event in events)


def test_alias_edit_action_still_denied_in_read_only_mode(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            '{"type":"tool_call","tool":"replace_range","parameters":{"path":"src/calc.py","start_line":2,"end_line":2,"replacement":"    return a + b\\n"}}',
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
    assert any(
        event["event_type"] == "agent_action" and event["metadata"].get("normalization_applied") is True for event in events
    )
    assert [event["event_type"] for event in events if event.get("tool_name") == "replace_range"] == [
        "agent_action",
        "policy_decision",
        "agent_observation",
    ]
