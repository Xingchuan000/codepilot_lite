from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codepilot.agent.actions import AgentFinishAction
from codepilot.agent.evidence import EvidenceDecision
from codepilot.agent.loop import MinimalAgentLoop, _resolve_finish
from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import LLMResponse, LLMToolCall, RichChatMessage
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter


def write_bug_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def _build_loop(tmp_path: Path, responses: list[LLMResponse], *, max_steps: int = 12) -> MinimalAgentLoop:
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="run-test",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode="build", interactive=False),
    )
    return MinimalAgentLoop(llm=StructuredFakeLLM(responses), router=router, max_steps=max_steps)


def test_native_tool_call_reaches_router_and_next_request_replays_rich_messages(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            LLMResponse(
                content="",
                tool_calls=(LLMToolCall("provider-call-1", "read_file", {"path": "src/calc.py"}),),
            ),
            LLMResponse(
                content="",
                tool_calls=(
                    LLMToolCall(
                        "provider-call-2",
                        "codepilot_finish",
                        {"status": "success", "summary": "Inspected the file.", "delivery_kind": "message"},
                    ),
                ),
            ),
        ],
    )

    result = loop.run("Inspect the file", repo)

    assert result.success is True
    assert result.status == "message_complete"
    calls = loop.llm.calls
    assert isinstance(calls[1]["messages"][-2], RichChatMessage)
    assert calls[1]["messages"][-2].role == "assistant"
    assert calls[1]["messages"][-2].parts[0].content["provider_tool_call_id"] == "provider-call-1"
    assert calls[1]["messages"][-1].role == "tool"
    assert calls[1]["messages"][-1].parts[0].content["provider_tool_call_id"] == "provider-call-1"


def test_multiple_native_tool_calls_are_routed_sequentially_and_replayed(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            LLMResponse(
                content="",
                tool_calls=(
                    LLMToolCall("provider-call-1", "read_file", {"path": "src/calc.py"}),
                    LLMToolCall("provider-call-2", "list_files", {"path": "src", "max_depth": 1}),
                ),
            ),
            LLMResponse(
                content="",
                tool_calls=(
                    LLMToolCall(
                        "provider-call-3",
                        "codepilot_finish",
                        {"status": "success", "summary": "Inspected both results.", "delivery_kind": "message"},
                    ),
                ),
            ),
        ],
    )

    assert loop.run("Inspect the repository", repo).success is True
    replay = loop.llm.calls[1]["messages"]
    assert [message.role for message in replay[-2:]] == ["tool", "tool"]
    assert [message.parts[0].content["provider_tool_call_id"] for message in replay[-2:]] == [
        "provider-call-1",
        "provider-call-2",
    ]
    events = [json.loads(line) for line in loop.router.trace_logger.trace_path.read_text(encoding="utf-8").splitlines()]
    llm_event = next(event for event in events if event["event_type"] == "llm_call")
    assert llm_event["metadata"]["native_tool_count"] == 2
    assert [event["metadata"]["provider_tool_call_id"] for event in events if event["event_type"] == "tool_call"] == [
        "provider-call-1",
        "provider-call-2",
    ]


def test_natural_text_after_write_requires_codepilot_finish_before_next_turn(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            LLMResponse(
                content="",
                tool_calls=(
                    LLMToolCall(
                        "provider-call-1",
                        "replace_range",
                        {
                            "path": "src/calc.py",
                            "start_line": 2,
                            "end_line": 2,
                            "replacement": "    return a + b\n",
                        },
                    ),
                ),
            ),
            LLMResponse(content="已经完成。"),
            LLMResponse(
                content="",
                tool_calls=(LLMToolCall("provider-call-3", "codepilot_finish", {"status": "partial", "summary": "Stopped."}),),
            ),
        ],
    )

    result = loop.run("Fix the bug", repo)

    assert result.status == "partial"
    guidance = loop.llm.calls[2]["messages"][-1]
    assert guidance.role == "user"
    assert "codepilot_finish" in guidance.content
    assert result.success is False


def test_native_parameter_value_error_is_returned_as_tool_result_without_repair(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    loop = _build_loop(
        tmp_path,
        [
            LLMResponse(
                content="",
                tool_calls=(LLMToolCall("provider-call-1", "read_file", {"path": ["src/calc.py"]}),),
            ),
            LLMResponse(
                content="",
                tool_calls=(
                    LLMToolCall("provider-call-2", "codepilot_finish", {"status": "partial", "summary": "Invalid input."}),
                ),
            ),
        ],
    )

    result = loop.run("Inspect the file", repo)

    assert result.status == "partial"
    tool_result = loop.llm.calls[1]["messages"][-1]
    assert tool_result.role == "tool"
    assert tool_result.parts[0].content["provider_tool_call_id"] == "provider-call-1"
    assert "Tool:" in tool_result.parts[0].content["content"]
    assert "src/calc.py" not in tool_result.parts[0].content["content"]


def test_native_finish_reuses_evidence_validation(tmp_path: Path) -> None:
    repo = write_bug_repo(tmp_path)
    result = _build_loop(
        tmp_path,
        [
            LLMResponse(
                content="",
                tool_calls=(
                    LLMToolCall(
                        "provider-call-1",
                        "codepilot_finish",
                        {"status": "success", "summary": "Claimed a change.", "changed_files": ["src/calc.py"]},
                    ),
                ),
            ),
            LLMResponse(
                content="",
                tool_calls=(LLMToolCall("provider-call-2", "codepilot_finish", {"status": "partial", "summary": "Needs work."}),),
            ),
        ],
    ).run("Fix the bug", repo)

    assert result.status == "partial"
    assert result.claimed_changed_files == ["src/calc.py"]
    assert result.missing_evidence


def test_natural_response_without_tool_calls_completes_normally(tmp_path: Path) -> None:
    result = _build_loop(tmp_path, [LLMResponse(content="Hello there")]).run("Say hello", write_bug_repo(tmp_path))

    assert result.success is True
    assert result.status == "message_complete"


def test_resolve_finish_keeps_evidence_decision_table() -> None:
    complete = EvidenceDecision(False, False, False, (), (), True)
    missing = EvidenceDecision(True, True, True, ("write_executed",), ("missing_passed_tests",), False)

    assert _resolve_finish(AgentFinishAction(status="failed", summary="failed"), delivery_kind="message", evidence=complete).completion_kind == "task_failed"
    assert _resolve_finish(AgentFinishAction(status="success", summary="blocked"), delivery_kind="code_change", evidence=missing).blocked_by_evidence is True
