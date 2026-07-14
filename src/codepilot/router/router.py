from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from codepilot.policy import PolicyChecker, PolicyContext, PolicyDecision
from codepilot.permissions import (
    PermissionBroker,
    PermissionRequest,
    PermissionResponse,
    make_permission_request_id,
    permission_now_iso,
)
from codepilot.router.actions import ToolAction, ToolRouteResult
from codepilot.session.permission import PermissionRequestContext, PermissionScopeBuilder
from codepilot.tools.base import ToolResult, ToolSpec
from codepilot.tools.registry import call_external_tool_traced, call_tool_traced, find_tool_spec
from codepilot.trace.logger import TraceLogger
from codepilot.trace.protocol import TraceRecorder


class ToolLifecycleObserver(Protocol):
    """工具持久化观察器；Router 不要求具体存储实现。"""

    def on_tool_call_created(self, action: ToolAction, spec: ToolSpec | None) -> str | None: ...
    def on_policy_denied(self, tool_call_id: str, result: ToolResult) -> None: ...
    def on_permission_pending(self, tool_call_id: str, request: PermissionRequest) -> None: ...
    def on_permission_resolved(self, tool_call_id: str, request: PermissionRequest, response: PermissionResponse | None, result: ToolResult | None = None) -> None: ...
    def build_recovery_token(self, action: ToolAction, spec: ToolSpec | None) -> dict[str, Any]: ...
    def on_pre_execution_failure(self, tool_call_id: str, error: Exception) -> None: ...
    def on_execution_started(self, tool_call_id: str, recovery_token: dict[str, Any]) -> None: ...
    def on_execution_finished(self, tool_call_id: str, result: ToolResult) -> None: ...
    def on_execution_exception(self, tool_call_id: str, error: Exception) -> None: ...


class _NoopToolLifecycleObserver:
    def on_tool_call_created(self, action: ToolAction, spec: ToolSpec | None) -> None:
        return None

    def on_policy_denied(self, tool_call_id: str, result: ToolResult) -> None:
        return None

    def on_permission_pending(self, tool_call_id: str, request: PermissionRequest) -> None:
        return None

    def on_permission_resolved(self, tool_call_id: str, request: PermissionRequest, response: PermissionResponse | None, result: ToolResult | None = None) -> None:
        return None

    def build_recovery_token(self, action: ToolAction, spec: ToolSpec | None) -> dict[str, Any]:
        return {}

    def on_execution_started(self, tool_call_id: str, recovery_token: dict[str, Any]) -> None:
        return None

    def on_pre_execution_failure(self, tool_call_id: str, error: Exception) -> None:
        return None

    def on_execution_finished(self, tool_call_id: str, result: ToolResult) -> None:
        return None

    def on_execution_exception(self, tool_call_id: str, error: Exception) -> None:
        return None


class ToolRouter:
    """把结构化 ToolAction 路由到 traced tool call。"""

    def __init__(
        self,
        trace_logger: TraceRecorder,
        output_preview_chars: int = 1000,
        policy_checker: PolicyChecker | None = None,
        policy_context: PolicyContext | None = None,
        external_tool_registry: Any | None = None,
        permission_broker: PermissionBroker | None = None,
        lifecycle_observer: ToolLifecycleObserver | None = None,
        permission_scope_builder: PermissionScopeBuilder | None = None,
        permission_request_context: PermissionRequestContext | None = None,
    ) -> None:
        self.trace_logger = trace_logger
        self.output_preview_chars = output_preview_chars
        self.policy_checker = policy_checker
        self.policy_context = policy_context or PolicyContext()
        self.external_tool_registry = external_tool_registry
        self.permission_broker = permission_broker
        self.lifecycle_observer = lifecycle_observer or _NoopToolLifecycleObserver()
        self.permission_scope_builder = permission_scope_builder or PermissionScopeBuilder()
        self.permission_request_context = permission_request_context

    @classmethod
    def from_runs_dir(
        cls,
        runs_dir: str | Path = "runs",
        run_id: str | None = None,
        output_preview_chars: int = 1000,
        policy_checker: PolicyChecker | None = None,
        policy_context: PolicyContext | None = None,
        external_tool_registry: Any | None = None,
        trace_logger: TraceRecorder | None = None,
        permission_broker: PermissionBroker | None = None,
        lifecycle_observer: ToolLifecycleObserver | None = None,
        permission_scope_builder: PermissionScopeBuilder | None = None,
        permission_request_context: PermissionRequestContext | None = None,
    ) -> "ToolRouter":
        logger = trace_logger or TraceLogger(runs_dir=runs_dir, run_id=run_id)
        return cls(
            trace_logger=logger,
            output_preview_chars=output_preview_chars,
            policy_checker=policy_checker,
            policy_context=policy_context,
            external_tool_registry=external_tool_registry,
            permission_broker=permission_broker,
            lifecycle_observer=lifecycle_observer,
            permission_scope_builder=permission_scope_builder,
            permission_request_context=permission_request_context,
        )

    def _base_route_metadata(self, parsed: ToolAction) -> dict[str, Any]:
        return {
            "run_id": self.trace_logger.run_id,
            "reason": parsed.reason,
            "arguments_keys": sorted(parsed.arguments.keys()),
            **parsed.metadata,
        }

    def _policy_metadata(self, decision: PolicyDecision) -> dict[str, Any]:
        return {
            "policy_decision": decision.decision,
            "policy_reason": decision.reason,
            "policy_rule": decision.matched_rule,
            "policy_mode": self.policy_context.mode,
            "requires_approval": decision.requires_approval,
            "approved": self.policy_context.approved,
        }

    def _permission_decision(self, response: PermissionResponse | None) -> Literal["approve_once", "approve_session", "deny"]:
        if response is not None and response.decision in {"approve_once", "approve_session"}:
            return response.decision
        return "deny"

    def route(self, action: ToolAction | Mapping[str, Any]) -> ToolRouteResult:
        """执行单个 tool action。"""

        parsed = ToolAction.model_validate(action)
        spec = find_tool_spec(parsed.tool_name)
        tool_call_id = self.lifecycle_observer.on_tool_call_created(parsed, spec)
        route_metadata = self._base_route_metadata(parsed)
        route_metadata["tool_call_id"] = tool_call_id
        policy_metadata: dict[str, Any] | None = None

        if self.policy_checker is not None:
            decision = self.policy_checker.check(parsed, context=self.policy_context)
            policy_metadata = self._policy_metadata(decision)
            policy_metadata.update(decision.metadata)

            self.trace_logger.record_policy_decision(
                tool_name=parsed.tool_name,
                decision=decision.decision,
                reason=decision.reason,
                rule=decision.matched_rule,
                mode=self.policy_context.mode,
                metadata=policy_metadata,
            )

            route_metadata.update(policy_metadata)

            if decision.denied:
                result = ToolResult(
                    success=False,
                    output="",
                    error=decision.reason,
                    metadata={
                        **policy_metadata,
                        "policy_violation": True,
                        "executed": False,
                    },
                )
                route_metadata.update(result.metadata)
                if tool_call_id is not None:
                    self.lifecycle_observer.on_policy_denied(tool_call_id, result)
                return ToolRouteResult(
                    action_id=parsed.action_id,
                    tool_name=parsed.tool_name,
                    success=False,
                    result=result,
                    trace_path=str(self.trace_logger.trace_path) if self.trace_logger.trace_path is not None else None,
                    error=result.error,
                    metadata=route_metadata,
                )

            if decision.asks and not self.policy_context.approved:
                if self.permission_broker is not None and self.policy_context.interactive:
                    request_id = make_permission_request_id()
                    workspace_root = Path(self.policy_context.repo).expanduser().resolve() if self.policy_context.repo is not None else Path(".").resolve()
                    scope = self.permission_scope_builder.build(parsed.tool_name, parsed.arguments, spec, workspace_root, decision.matched_rule)
                    request = PermissionRequest(
                        request_id=request_id,
                        run_id=self.trace_logger.run_id,
                        action_id=parsed.action_id,
                        tool_name=parsed.tool_name,
                        arguments_preview=parsed.arguments,
                        reason=decision.reason,
                        risk=policy_metadata.get("risk") if isinstance(policy_metadata.get("risk"), str) else None,
                        side_effect=policy_metadata.get("side_effect") if isinstance(policy_metadata.get("side_effect"), str) else None,
                        matched_rule=decision.matched_rule,
                        created_at=permission_now_iso(),
                        tool_call_id=tool_call_id,
                        session_id=self.permission_request_context.session_id if self.permission_request_context is not None else None,
                        turn_id=self.permission_request_context.turn_id if self.permission_request_context is not None else None,
                        attempt_id=self.permission_request_context.attempt_id if self.permission_request_context is not None else None,
                        scope_key=scope.key,
                        scope_json=scope.data,
                    )
                    if tool_call_id is not None:
                        self.lifecycle_observer.on_permission_pending(tool_call_id, request)
                    self.permission_broker.request(request)
                    self.trace_logger.record_permission_request(
                        request_id=request_id,
                        tool_name=parsed.tool_name,
                        reason=decision.reason,
                        metadata={
                            "action_id": parsed.action_id,
                            "arguments_preview": parsed.arguments,
                            "risk": policy_metadata.get("risk"),
                            "side_effect": policy_metadata.get("side_effect"),
                            "matched_rule": decision.matched_rule,
                        },
                    )
                    response = self.permission_broker.wait(request.request_id)
                    decision_value = self._permission_decision(response)
                    self.trace_logger.record_permission_response(
                        request_id=request.request_id,
                        decision=decision_value,
                        reason=response.reason if response is not None else decision.reason,
                        metadata={"action_id": parsed.action_id},
                    )
                    if decision_value not in {"approve_once", "approve_session"}:
                        result = ToolResult(
                            success=False,
                            output="",
                            error=response.reason if response is not None else decision.reason,
                            metadata={
                                **policy_metadata,
                                "requires_approval": True,
                                "approved": False,
                                "executed": False,
                            },
                        )
                        route_metadata.update(result.metadata)
                        if tool_call_id is not None:
                            self.lifecycle_observer.on_permission_resolved(tool_call_id, request, response, result)
                        return ToolRouteResult(
                            action_id=parsed.action_id,
                            tool_name=parsed.tool_name,
                            success=False,
                            result=result,
                            trace_path=str(self.trace_logger.trace_path) if self.trace_logger.trace_path is not None else None,
                            error=result.error,
                            metadata=route_metadata,
                        )
                    policy_metadata["approved"] = True
                    if tool_call_id is not None:
                        self.lifecycle_observer.on_permission_resolved(tool_call_id, request, response)
                else:
                    result = ToolResult(
                        success=False,
                        output="",
                        error=decision.reason,
                        metadata={
                            **policy_metadata,
                            "requires_approval": True,
                            "approved": False,
                            "executed": False,
                        },
                    )
                    route_metadata.update(result.metadata)
                    if tool_call_id is not None:
                        self.lifecycle_observer.on_policy_denied(tool_call_id, result)
                    return ToolRouteResult(
                        action_id=parsed.action_id,
                        tool_name=parsed.tool_name,
                        success=False,
                        result=result,
                        trace_path=str(self.trace_logger.trace_path) if self.trace_logger.trace_path is not None else None,
                        error=result.error,
                        metadata=route_metadata,
                    )

        try:
            recovery_token = self.lifecycle_observer.build_recovery_token(parsed, spec)
        except Exception as exc:
            if tool_call_id is not None:
                self.lifecycle_observer.on_pre_execution_failure(tool_call_id, exc)
            raise
        if tool_call_id is not None:
            self.lifecycle_observer.on_execution_started(tool_call_id, recovery_token)
        try:
            if self.external_tool_registry is not None and self.external_tool_registry.has_tool(parsed.tool_name):
                result = call_external_tool_traced(
                    parsed.tool_name,
                    external_registry=self.external_tool_registry,
                    trace_logger=self.trace_logger,
                    output_preview_chars=self.output_preview_chars,
                    **parsed.arguments,
                )
            else:
                result = call_tool_traced(
                    parsed.tool_name,
                    trace_logger=self.trace_logger,
                    output_preview_chars=self.output_preview_chars,
                    **parsed.arguments,
                )
        except Exception as exc:
            if tool_call_id is not None:
                self.lifecycle_observer.on_execution_exception(tool_call_id, exc)
            raise
        if tool_call_id is not None:
            self.lifecycle_observer.on_execution_finished(tool_call_id, result)

        if policy_metadata is not None:
            merged_result_metadata = {
                **result.metadata,
                **policy_metadata,
                "executed": True,
            }
            result = result.model_copy(update={"metadata": merged_result_metadata})
            route_metadata.update(merged_result_metadata)

        return ToolRouteResult(
            action_id=parsed.action_id,
            tool_name=parsed.tool_name,
            success=result.success,
            result=result,
            trace_path=str(self.trace_logger.trace_path) if self.trace_logger.trace_path is not None else None,
            error=result.error,
            metadata=route_metadata,
        )

    def route_many(
        self,
        actions: Sequence[ToolAction | Mapping[str, Any]],
        task: str | None = None,
        record_run_events: bool = True,
    ) -> list[ToolRouteResult]:
        """按顺序执行多个 tool action。"""

        if record_run_events:
            self.trace_logger.record_run_start(task=task, metadata={"source": "tool_router"})

        results: list[ToolRouteResult] = []
        for action in actions:
            results.append(self.route(action))

        if record_run_events:
            self.trace_logger.record_run_end(
                success=all(item.success for item in results),
                summary=f"Routed {len(results)} tool action(s).",
                metadata={"source": "tool_router"},
            )

        return results
