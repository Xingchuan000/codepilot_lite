from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from codepilot.policy import PolicyChecker, PolicyContext, PolicyDecision
from codepilot.permissions import (
    PermissionBroker,
    PermissionRequest,
    PermissionResponse,
    make_permission_request_id,
    permission_now_iso,
)
from codepilot.router.actions import ToolAction, ToolRouteResult
from codepilot.tools.base import ToolResult
from codepilot.tools.registry import call_external_tool_traced, call_tool_traced
from codepilot.trace.logger import TraceLogger
from codepilot.trace.protocol import TraceRecorder


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
    ) -> None:
        self.trace_logger = trace_logger
        self.output_preview_chars = output_preview_chars
        self.policy_checker = policy_checker
        self.policy_context = policy_context or PolicyContext()
        self.external_tool_registry = external_tool_registry
        self.permission_broker = permission_broker

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
    ) -> "ToolRouter":
        logger = trace_logger or TraceLogger(runs_dir=runs_dir, run_id=run_id)
        return cls(
            trace_logger=logger,
            output_preview_chars=output_preview_chars,
            policy_checker=policy_checker,
            policy_context=policy_context,
            external_tool_registry=external_tool_registry,
            permission_broker=permission_broker,
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

    def _permission_decision(self, response: PermissionResponse | None) -> Literal["approve_once", "deny"]:
        if response is not None and response.decision == "approve_once":
            return "approve_once"
        return "deny"

    def route(self, action: ToolAction | Mapping[str, Any]) -> ToolRouteResult:
        """执行单个 tool action。"""

        parsed = ToolAction.model_validate(action)
        route_metadata = self._base_route_metadata(parsed)
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
                return ToolRouteResult(
                    action_id=parsed.action_id,
                    tool_name=parsed.tool_name,
                    success=False,
                    result=result,
                    trace_path=str(self.trace_logger.trace_path),
                    error=result.error,
                    metadata=route_metadata,
                )

            if decision.asks and not self.policy_context.approved:
                if self.permission_broker is not None and self.policy_context.interactive:
                    request_id = make_permission_request_id()
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
                    )
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
                    if decision_value != "approve_once":
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
                        return ToolRouteResult(
                            action_id=parsed.action_id,
                            tool_name=parsed.tool_name,
                            success=False,
                            result=result,
                            trace_path=str(self.trace_logger.trace_path),
                            error=result.error,
                            metadata=route_metadata,
                        )
                    policy_metadata["approved"] = True
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
                    return ToolRouteResult(
                        action_id=parsed.action_id,
                        tool_name=parsed.tool_name,
                        success=False,
                        result=result,
                        trace_path=str(self.trace_logger.trace_path),
                        error=result.error,
                        metadata=route_metadata,
                    )

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
            trace_path=str(self.trace_logger.trace_path),
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
