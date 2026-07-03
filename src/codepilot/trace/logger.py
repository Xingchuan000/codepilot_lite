from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from codepilot.trace.events import TraceEvent

MAX_TRACE_PREVIEW_CHARS = 1000


def make_run_id(prefix: str = "run") -> str:
    """生成一个短而稳定的 run_id。"""

    return f"{prefix}-{uuid4().hex[:12]}"


def _preview_text(text: str | None, max_chars: int = MAX_TRACE_PREVIEW_CHARS) -> tuple[str, bool]:
    """生成适合 trace 的短预览，避免把长文本完整写入。"""

    if not text:
        return "", False
    if len(text) <= max_chars:
        return text, False
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}", True


class TraceLogger:
    """把结构化 TraceEvent 追加写入 trace.jsonl。"""

    def __init__(self, runs_dir: str | Path = "runs", run_id: str | None = None) -> None:
        self.runs_dir = Path(runs_dir)
        self.run_id = run_id or make_run_id()
        self.run_dir = self.runs_dir / self.run_id
        self.trace_path = self.run_dir / "trace.jsonl"

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._step = self._load_existing_step()

    def _load_existing_step(self) -> int:
        """同一个 run_id 再次打开时，继续已有最大 step。"""

        if not self.trace_path.exists():
            return 0

        max_step = 0
        for line in self.trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            step = data.get("step")
            if isinstance(step, int):
                max_step = max(max_step, step)
        return max_step

    @property
    def next_step(self) -> int:
        self._step += 1
        return self._step

    def record(self, event: TraceEvent) -> TraceEvent:
        """把单条 trace 事件追加到 jsonl 文件。"""

        with self.trace_path.open("a", encoding="utf-8") as file:
            file.write(event.model_dump_json() + "\n")
        return event

    def record_policy_decision(
        self,
        tool_name: str,
        decision: str,
        reason: str,
        rule: str | None = None,
        mode: str | None = None,
        metadata: dict | None = None,
    ) -> TraceEvent:
        """记录一次策略判断，方便后续追踪为什么允许或拒绝动作。"""

        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="policy_decision",
            tool_name=tool_name,
            policy_decision=decision,
            policy_reason=reason,
            policy_rule=rule,
            policy_mode=mode,
            metadata=metadata or {},
        )
        return self.record(event)

    def record_llm_call(
        self,
        *,
        model: str | None = None,
        message_count: int,
        response_text: str | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
    ) -> TraceEvent:
        """记录一次 LLM 调用结果。"""

        output_preview, truncated = _preview_text(response_text)
        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="llm_call",
            success=True,
            output_preview=output_preview,
            metadata={
                "model": model,
                "message_count": message_count,
                "response_chars": len(response_text or ""),
                "response_preview_truncated": truncated,
                "usage": usage or {},
                **(metadata or {}),
            },
        )
        return self.record(event)

    def record_agent_action(
        self,
        *,
        action_type: str | None,
        tool_name: str | None = None,
        input: dict | None = None,
        success: bool,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> TraceEvent:
        """记录一次模型产出的结构化动作。"""

        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="agent_action",
            tool_name=tool_name,
            input=input or {},
            success=success,
            error=error,
            metadata={"action_type": action_type, **(metadata or {})},
        )
        return self.record(event)

    def record_agent_observation(
        self,
        *,
        tool_name: str | None,
        observation: str,
        metadata: dict | None = None,
    ) -> TraceEvent:
        """记录返回给模型的 observation。"""

        output_preview, truncated = _preview_text(observation)
        summary = observation.strip().splitlines()[0] if observation.strip() else ""
        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="agent_observation",
            tool_name=tool_name,
            output_summary=summary or None,
            output_preview=output_preview,
            metadata={
                "observation_chars": len(observation),
                "observation_preview_truncated": truncated,
                **(metadata or {}),
            },
        )
        return self.record(event)

    def record_agent_finish(
        self,
        *,
        status: str,
        summary: str,
        metadata: dict | None = None,
    ) -> TraceEvent:
        """记录 agent 主动结束运行。"""

        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="agent_finish",
            success=status == "success",
            output_summary=summary,
            metadata={"status": status, **(metadata or {})},
        )
        return self.record(event)

    def record_run_start(self, task: str | None = None, metadata: dict | None = None) -> TraceEvent:
        """记录 run 开始事件。"""

        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="run_start",
            metadata={"task": task, **(metadata or {})},
        )
        return self.record(event)

    def record_run_end(
        self,
        success: bool = True,
        summary: str | None = None,
        metadata: dict | None = None,
    ) -> TraceEvent:
        """记录 run 结束事件。"""

        event = TraceEvent(
            run_id=self.run_id,
            step=self.next_step,
            event_type="run_end",
            success=success,
            output_summary=summary,
            metadata=metadata or {},
        )
        return self.record(event)
