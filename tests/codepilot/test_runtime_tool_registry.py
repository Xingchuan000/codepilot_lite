from __future__ import annotations

import json
from pathlib import Path

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


def _spec(name: str = "runtime_echo") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Echo a runtime argument.",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        idempotency=ToolIdempotency.SAFE,
        recovery_strategy=ToolRecoveryStrategy.AUTO_RETRY,
        parameters={"value": "text"},
        metadata={"source": "runtime"},
    )


class _Lifecycle:
    def __init__(self) -> None:
        self.spec = None
        self.finished = None

    def on_tool_call_created(self, action, spec):
        self.spec = spec
        return "tool-call-1"

    def on_policy_denied(self, tool_call_id, result):
        pass

    def on_permission_pending(self, tool_call_id, request):
        pass

    def on_permission_resolved(self, tool_call_id, request, response, result=None):
        pass

    def build_recovery_token(self, action, spec):
        return {"tool": action.tool_name}

    def on_pre_execution_failure(self, tool_call_id, error):
        pass

    def on_execution_started(self, tool_call_id, recovery_token):
        pass

    def on_execution_finished(self, tool_call_id, result):
        self.finished = result

    def on_execution_exception(self, tool_call_id, error):
        pass


def test_runtime_registry_validates_handlers_and_calls_typed_result() -> None:
    registry = RuntimeToolRegistry([_spec()], {"runtime_echo": lambda args: ToolResult(success=True, output=args["value"])})

    assert registry.find_spec("runtime_echo") == _spec()
    assert registry.call("runtime_echo", {"value": "ok"}).output == "ok"
    assert not registry.has_tool("missing")
    assert registry.call("missing", {}).success is False


def test_router_resolves_runtime_spec_for_policy_lifecycle_and_trace(tmp_path: Path) -> None:
    lifecycle = _Lifecycle()
    registry = RuntimeToolRegistry(
        [_spec()],
        {"runtime_echo": lambda args: ToolResult(success=True, output=f"hello {args['value']}")},
    )
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="runtime-test",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode="build", approved=True),
        runtime_tool_registry=registry,
        lifecycle_observer=lifecycle,
    )

    result = router.route({"tool_name": "runtime_echo", "arguments": {"value": "world"}})

    assert result.success is True
    assert result.result.output == "hello world"
    assert lifecycle.spec == _spec()
    assert lifecycle.finished is not None
    events = [json.loads(line) for line in router.trace_logger.trace_path.read_text(encoding="utf-8").splitlines()]
    tool_event = next(event for event in events if event["event_type"] == "tool_call")
    assert tool_event["tool_name"] == "runtime_echo"
    assert tool_event["risk"] == "read_only"
    assert tool_event["metadata"]["runtime_tool"] is True


def test_router_keeps_unknown_tool_structured_error(tmp_path: Path) -> None:
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs", run_id="unknown-test", policy_checker=PolicyChecker.default())

    result = router.route({"tool_name": "not_registered", "arguments": {}})

    assert result.success is False
    assert result.result.error == "Unknown tool: not_registered"


def test_router_rejects_a_hidden_tool_even_when_the_model_guesses_its_name(tmp_path: Path) -> None:
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="profile-boundary",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode="build", approved=True),
        allowed_tool_names={"read_file"},
    )

    result = router.route({"tool_name": "run_shell", "arguments": {"repo": tmp_path, "command": "echo unsafe"}})

    assert result.success is False
    assert result.result.metadata["policy_rule"] == "agent.profile.tool.deny"
