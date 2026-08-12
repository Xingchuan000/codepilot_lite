from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from codepilot.agent.boundary import RuntimeToolContext
from codepilot.multi_agent.models import SpawnContract
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.router.runtime_tools import RuntimeToolRegistry
from codepilot.tools.base import (
    ExternalImpact,
    Reversibility,
    ToolIdempotency,
    ToolRecoveryStrategy,
    ToolResult,
    ToolRisk,
    ToolSideEffect,
    ToolSpec,
)


class SpawnAgentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_type: Literal["general", "explore", "scout"]
    task: str = Field(min_length=1)
    write_scope: list[str] = Field(default_factory=list)
    context_mode: Literal["none", "recent", "summary", "summary_recent", "full"] = "summary_recent"
    recent_turns: int = Field(default=3, ge=0, le=10)


class AgentIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)


class WaitAgentArgs(AgentIdArgs):
    timeout_seconds: float | None = Field(default=None, gt=0, le=30)


class _EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _spec(
    name: str,
    description: str,
    risk: ToolRisk,
    side_effect: ToolSideEffect,
    external_impact: ExternalImpact,
    reversibility: Reversibility,
    parameters: dict[str, Any],
    *,
    recovery: ToolRecoveryStrategy = ToolRecoveryStrategy.ASK_USER,
) -> ToolSpec:
    argument_models: dict[str, type[BaseModel]] = {
        "spawn_agent": SpawnAgentArgs,
        "wait_agent": WaitAgentArgs,
        "list_agents": _EmptyArgs,
        "close_agent": AgentIdArgs,
        "inspect_agent_patch": AgentIdArgs,
        "apply_agent_patch": AgentIdArgs,
    }
    return ToolSpec(
        name=name,
        description=description,
        risk=risk,
        side_effect=side_effect,
        external_impact=external_impact,
        reversibility=reversibility,
        idempotency=ToolIdempotency.CONDITIONAL,
        recovery_strategy=recovery,
        input_schema=argument_models[name].model_json_schema(),
        parameters=parameters,
        metadata={"source": "runtime_agent_control"},
    )


def _tool_specs() -> tuple[ToolSpec, ...]:
    return (
        _spec(
            "spawn_agent",
            (
                "Spawn one delegated child agent for a bounded task. Optional fields may be omitted "
                "when their defaults are acceptable; do not invent placeholder values."
            ),
            ToolRisk.LOCAL_EXECUTION,
            ToolSideEffect.LOCAL_EXEC,
            ExternalImpact.NONE,
            Reversibility.REVERSIBLE,
            {
                "agent_type": 'required string enum: "general" | "explore" | "scout"',
                "task": "required non-empty string containing one self-contained delegated task",
                "write_scope": (
                    "optional JSON array[string] of repository-relative paths; default []; "
                    "use [] for Explore/Scout/read-only work; never pass a string or null"
                ),
                "context_mode": (
                    'optional string enum: "none" | "recent" | "summary" | "summary_recent" | "full"; '
                    'default "summary_recent"; never pass prose such as "fork mode"'
                ),
                "recent_turns": "optional integer 0..10; default 3",
            },
            recovery=ToolRecoveryStrategy.RECONCILE_OR_ASK,
        ),
        _spec(
            "wait_agent",
            (
                "Wait for one delegated child agent for at most 30 seconds of child execution time. "
                "Time spent waiting for a user permission decision does not consume this timeout; "
                "the wait remains blocked until that decision is resolved. If the child is still "
                "running afterward, use another bounded wait later."
            ),
            ToolRisk.READ_ONLY,
            ToolSideEffect.NONE,
            ExternalImpact.NONE,
            Reversibility.NOT_APPLICABLE,
            {
                "agent_id": "required non-empty string: child session id returned by spawn_agent",
                "timeout_seconds": "optional number > 0 and <= 30; omit to use the supervisor default",
            },
            recovery=ToolRecoveryStrategy.AUTO_RETRY,
        ),
        _spec(
            "list_agents",
            "List child agents from the SQLite session tree.",
            ToolRisk.READ_ONLY,
            ToolSideEffect.NONE,
            ExternalImpact.NONE,
            Reversibility.NOT_APPLICABLE,
            {},
            recovery=ToolRecoveryStrategy.AUTO_RETRY,
        ),
        _spec(
            "close_agent",
            "Cancel a running delegated child agent.",
            ToolRisk.LOCAL_EXECUTION,
            ToolSideEffect.LOCAL_EXEC,
            ExternalImpact.NONE,
            Reversibility.REVERSIBLE,
            {"agent_id": "child session id"},
            recovery=ToolRecoveryStrategy.RECONCILE_OR_ASK,
        ),
        _spec(
            "inspect_agent_patch",
            "Inspect a completed General child patch without changing the Primary workspace.",
            ToolRisk.READ_ONLY,
            ToolSideEffect.NONE,
            ExternalImpact.NONE,
            Reversibility.NOT_APPLICABLE,
            {"agent_id": "child session id"},
            recovery=ToolRecoveryStrategy.AUTO_RETRY,
        ),
        _spec(
            "apply_agent_patch",
            "Apply an already reviewed child-agent patch into the current Primary workspace.",
            ToolRisk.LOCAL_WRITE,
            ToolSideEffect.LOCAL_WRITE,
            ExternalImpact.NONE,
            Reversibility.REVERSIBLE,
            {"agent_id": "child session id"},
            recovery=ToolRecoveryStrategy.ASK_USER,
        ),
    )


def _success(value: dict[str, object]) -> ToolResult:
    return ToolResult(success=True, output=json.dumps(value, ensure_ascii=False), metadata=value)


def _failure(error: Exception) -> ToolResult:
    return ToolResult(success=False, error=str(error), metadata={"executed": False})


def build_agent_control_registry(
    supervisor: AgentSupervisor,
    context: RuntimeToolContext,
) -> RuntimeToolRegistry:
    def spawn(arguments: dict[str, Any]) -> ToolResult:
        try:
            args = SpawnAgentArgs.model_validate(arguments)
            return _success(
                supervisor.spawn(
                    context=context,
                    contract=SpawnContract(
                        agent_type=args.agent_type,
                        task=args.task,
                        write_scope=tuple(args.write_scope),
                        context_mode=args.context_mode,
                        recent_turns=args.recent_turns,
                    ),
                )
            )
        except (LookupError, ValidationError, ValueError, PermissionError, RuntimeError) as exc:
            return _failure(exc)

    def wait(arguments: dict[str, Any]) -> ToolResult:
        try:
            args = WaitAgentArgs.model_validate(arguments)
            return _success(supervisor.wait(context.parent_session_id, args.agent_id, args.timeout_seconds))
        except (LookupError, ValidationError, ValueError, PermissionError, RuntimeError) as exc:
            return _failure(exc)

    def list_agents(arguments: dict[str, Any]) -> ToolResult:
        if arguments:
            return ToolResult(success=False, error="list_agents does not accept arguments", metadata={"executed": False})
        return _success({"agents": supervisor.list_agents(context.parent_session_id)})

    def close(arguments: dict[str, Any]) -> ToolResult:
        try:
            args = AgentIdArgs.model_validate(arguments)
            return _success(supervisor.close(context.parent_session_id, args.agent_id))
        except (LookupError, ValidationError, ValueError, PermissionError, RuntimeError) as exc:
            return _failure(exc)

    def inspect(arguments: dict[str, Any]) -> ToolResult:
        try:
            args = AgentIdArgs.model_validate(arguments)
            return _success(supervisor.inspect_agent_patch(context.parent_session_id, args.agent_id))
        except (LookupError, ValidationError, ValueError, PermissionError, RuntimeError) as exc:
            return _failure(exc)

    def apply(arguments: dict[str, Any]) -> ToolResult:
        try:
            args = AgentIdArgs.model_validate(arguments)
            return supervisor.apply_agent_patch(context.parent_session_id, args.agent_id, context.parent_repo)
        except (LookupError, ValidationError, ValueError, PermissionError, RuntimeError) as exc:
            return _failure(exc)

    handlers = {
        "spawn_agent": spawn,
        "wait_agent": wait,
        "list_agents": list_agents,
        "close_agent": close,
        "inspect_agent_patch": inspect,
        "apply_agent_patch": apply,
    }
    return RuntimeToolRegistry(_tool_specs(), handlers)
