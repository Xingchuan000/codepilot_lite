from __future__ import annotations

from pathlib import Path

from codepilot.agent.loop import MinimalAgentLoop, _inject_repo_if_required
from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import LLMResponse, LLMToolCall
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.router.runtime_tools import RuntimeToolRegistry
from codepilot.tools.base import (
    DefaultPermission,
    ToolIdempotency,
    ToolRecoveryStrategy,
    ToolResult,
    ToolRisk,
    ToolSideEffect,
    ToolSpec,
)


def _runtime_spec() -> ToolSpec:
    return ToolSpec(
        name="spawn_agent",
        description="Spawn a delegated agent.",
        risk=ToolRisk.LOCAL_EXECUTION,
        side_effect=ToolSideEffect.LOCAL_EXEC,
        default_permission=DefaultPermission.ALLOW,
        idempotency=ToolIdempotency.CONDITIONAL,
        recovery_strategy=ToolRecoveryStrategy.RECONCILE_OR_ASK,
        parameters={"agent_type": "agent type", "task": "task"},
    )


def test_repo_is_injected_only_when_tool_declares_repo() -> None:
    repo = Path("/tmp/repo")
    builtin = ToolSpec(
        name="read_file",
        description="read",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        parameters={"repo": "repo", "path": "path"},
        inject_repo=True,
    )

    assert _inject_repo_if_required({"path": "README.md"}, repo, builtin) == {"path": "README.md", "repo": str(repo)}
    assert _inject_repo_if_required({"task": "inspect"}, repo, _runtime_spec()) == {"task": "inspect"}
    assert _inject_repo_if_required({"repo": "/unrelated"}, repo, None) == {"repo": "/unrelated"}


def test_loop_passes_runtime_tool_arguments_without_forced_repo(tmp_path: Path) -> None:
    received: list[dict[str, object]] = []
    registry = RuntimeToolRegistry(
        [_runtime_spec()],
        {
            "spawn_agent": lambda args: (
                received.append(dict(args)) or ToolResult(success=True, output="spawned")
            )
        },
    )
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="repo-injection-test",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode="build", approved=True),
        runtime_tool_registry=registry,
    )
    loop = MinimalAgentLoop(
        llm=StructuredFakeLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=(LLMToolCall(provider_tool_call_id="provider-1", name="spawn_agent", arguments={"agent_type": "explore", "task": "inspect"}),),
                ),
                LLMResponse(content="finished"),
            ]
        ),
        router=router,
        visible_tool_specs=registry.list_specs(),
    )

    result = loop.run("delegate", tmp_path)

    assert result.success is True
    assert received == [{"agent_type": "explore", "task": "inspect"}]
