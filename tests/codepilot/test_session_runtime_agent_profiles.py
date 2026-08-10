from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.agent.boundary import RuntimeToolContext
from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import LLMResponse, LLMToolCall
from codepilot.multi_agent.boundary import MultiAgentBoundaryResolver
from codepilot.multi_agent.profiles import EXPLORE_PROFILE, GENERAL_PROFILE
from codepilot.multi_agent.runtime_tools import build_agent_control_registry
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.session.database import SessionDatabase
from codepilot.session.models import TurnSubmission
from codepilot.session.runtime import SessionRuntime
from codepilot.session.service import SessionService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _session(tmp_path: Path) -> tuple[SessionDatabase, SessionService, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "demo@example.com")
    _git(repo, "config", "user.name", "Demo")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    service = SessionService(database)
    session = service.create_session(repo, "openai", "gpt-4.1", "manual")
    return database, service, session.session_id, repo


def test_session_runtime_binds_explore_profile_to_prompt_and_router(tmp_path: Path) -> None:
    database, service, session_id, repo = _session(tmp_path)
    llm = StructuredFakeLLM(
        [
            LLMResponse(content="", tool_calls=(LLMToolCall(provider_tool_call_id="provider-1", name="run_shell", arguments={"command": "echo hidden"}),)),
            LLMResponse(content="done"),
        ]
    )

    def router_factory(trace):  # noqa: ANN001
        return ToolRouter(
            trace,
            policy_checker=PolicyChecker.default(),
            policy_context=PolicyContext(repo=repo, mode="build", approved=True),
        )

    runtime = SessionRuntime(database, llm, router_factory, boundary_resolver=MultiAgentBoundaryResolver(EXPLORE_PROFILE))
    submission = runtime.submit_user_message(session_id, "inspect")
    assert isinstance(submission, TurnSubmission)

    execution = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    assert execution.result.status == "message_complete"
    assert "- name: run_shell" not in str(llm.calls[0]["messages"][0].content)
    assert service.store.tool_executions.list_tool_calls(session_id)[0].status == "denied"


def test_session_runtime_general_writer_carries_write_scope_to_policy(tmp_path: Path) -> None:
    database, service, session_id, repo = _session(tmp_path)
    llm = StructuredFakeLLM(
        [
            LLMResponse(content="", tool_calls=(LLMToolCall(provider_tool_call_id="provider-2", name="replace_range", arguments={"path": "README.md", "start_line": 1, "end_line": 1, "replacement": "unsafe\n"}),)),
            LLMResponse(content="done"),
        ]
    )

    def router_factory(trace):  # noqa: ANN001
        return ToolRouter(
            trace,
            policy_checker=PolicyChecker.default(),
            policy_context=PolicyContext(repo=repo, mode="build", approved=True),
        )

    runtime = SessionRuntime(
        database,
        llm,
        router_factory,
        boundary_resolver=MultiAgentBoundaryResolver(GENERAL_PROFILE, ("src/**",)),
    )
    submission = runtime.submit_user_message(session_id, "inspect")
    assert isinstance(submission, TurnSubmission)
    runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    assert service.store.tool_executions.list_tool_calls(session_id)[0].status == "denied"
    assert "outside the current agent write_scope" in (service.store.tool_executions.list_tool_results(session_id)[0].error or "")


def test_session_runtime_general_writer_enforces_scope_without_policy_checker(tmp_path: Path) -> None:
    database, service, session_id, repo = _session(tmp_path)
    llm = StructuredFakeLLM(
        [
            LLMResponse(content="", tool_calls=(LLMToolCall(provider_tool_call_id="provider-3", name="replace_range", arguments={"path": "README.md", "start_line": 1, "end_line": 1, "replacement": "unsafe\n"}),)),
            LLMResponse(content="done"),
        ]
    )

    runtime = SessionRuntime(
        database,
        llm,
        lambda trace: ToolRouter(trace),
        boundary_resolver=MultiAgentBoundaryResolver(GENERAL_PROFILE, ("src/**",)),
    )
    submission = runtime.submit_user_message(session_id, "inspect")
    assert isinstance(submission, TurnSubmission)
    runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    assert service.store.tool_executions.list_tool_calls(session_id)[0].status == "denied"
    assert (repo / "README.md").read_text(encoding="utf-8") == "demo\n"


def test_session_runtime_injects_primary_agent_controls_as_runtime_tools(tmp_path: Path) -> None:
    database, service, session_id, repo = _session(tmp_path)
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    llm = StructuredFakeLLM(
        [
            LLMResponse(content="", tool_calls=(LLMToolCall(provider_tool_call_id="provider-4", name="list_agents", arguments={}),)),
            LLMResponse(content="done"),
        ]
    )

    def router_factory(trace):  # noqa: ANN001
        return ToolRouter(
            trace,
            policy_checker=PolicyChecker.default(),
            policy_context=PolicyContext(repo=repo, mode="build", approved=True),
        )

    def control_registry(context: RuntimeToolContext):
        return build_agent_control_registry(supervisor, context)

    runtime = SessionRuntime(database, llm, router_factory, runtime_tool_registry_factory=control_registry)
    submission = runtime.submit_user_message(session_id, "delegate")
    assert isinstance(submission, TurnSubmission)
    execution = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    assert execution.result.status == "message_complete"
    assert "list_agents" in {spec.name for spec in llm.calls[0]["tools"]}
    assert '"agents": []' in (service.store.tool_executions.list_tool_results(session_id)[0].content or "")

