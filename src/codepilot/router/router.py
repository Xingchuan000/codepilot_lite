from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from codepilot.router.actions import ToolAction, ToolRouteResult
from codepilot.tools.registry import call_tool_traced
from codepilot.trace.logger import TraceLogger


class ToolRouter:
    """把结构化 ToolAction 路由到 traced tool call。"""

    def __init__(
        self,
        trace_logger: TraceLogger,
        output_preview_chars: int = 1000,
    ) -> None:
        self.trace_logger = trace_logger
        self.output_preview_chars = output_preview_chars

    @classmethod
    def from_runs_dir(
        cls,
        runs_dir: str | Path = "runs",
        run_id: str | None = None,
        output_preview_chars: int = 1000,
    ) -> "ToolRouter":
        logger = TraceLogger(runs_dir=runs_dir, run_id=run_id)
        return cls(trace_logger=logger, output_preview_chars=output_preview_chars)

    def route(self, action: ToolAction | Mapping[str, Any]) -> ToolRouteResult:
        """执行单个 tool action。"""

        parsed = ToolAction.model_validate(action)
        result = call_tool_traced(
            parsed.tool_name,
            trace_logger=self.trace_logger,
            output_preview_chars=self.output_preview_chars,
            **parsed.arguments,
        )
        return ToolRouteResult(
            action_id=parsed.action_id,
            tool_name=parsed.tool_name,
            success=result.success,
            result=result,
            trace_path=str(self.trace_logger.trace_path),
            error=result.error,
            metadata={
                "run_id": self.trace_logger.run_id,
                "reason": parsed.reason,
                "arguments_keys": sorted(parsed.arguments.keys()),
                **parsed.metadata,
            },
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
