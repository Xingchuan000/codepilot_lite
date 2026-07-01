from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from codepilot.trace.events import TraceEvent


def make_run_id(prefix: str = "run") -> str:
    """生成一个短而稳定的 run_id。"""

    return f"{prefix}-{uuid4().hex[:12]}"


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
