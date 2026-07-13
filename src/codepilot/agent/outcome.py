from __future__ import annotations

from dataclasses import dataclass

from codepilot.agent.evidence import AssistantStopReason, CompletionKind, EvidenceSnapshot
from codepilot.agent.state import AgentState, evidence_snapshot


@dataclass(frozen=True)
class RunOutcomeSnapshot:
    """集中保存一次 Agent 运行的最终状态和证据。

    该对象是 Agent、TUI 结束事件和 Session RunRef 的共同数据源，防止多个调用方
    分别复制字段后产生遗漏或命名差异。集合字段保持 tuple，仅在序列化边界转为 list。
    """

    status: str
    completion_kind: CompletionKind | None
    assistant_stop_reason: AssistantStopReason | None
    delivery_kind: str | None
    evidence: EvidenceSnapshot
    changed_files: tuple[str, ...]
    last_test_status: str | None

    def to_payload(self) -> dict[str, object]:
        """生成符合当前 TUI/Trace 契约的顶层字段。"""

        return {
            "status": self.status,
            "completion_kind": self.completion_kind,
            "assistant_stop_reason": self.assistant_stop_reason,
            "delivery_kind": self.delivery_kind,
            "changed_files": list(self.changed_files),
            "test_status": self.last_test_status,
            **self.evidence.to_payload(),
        }


def build_run_outcome(state: AgentState, *, status: str) -> RunOutcomeSnapshot:
    """从 AgentState 一次性构造运行结果，避免结束链路逐字段搬运。"""

    return RunOutcomeSnapshot(
        status=status,
        completion_kind=state.completion_kind,
        assistant_stop_reason=state.assistant_stop_reason,
        delivery_kind=state.delivery_kind,
        evidence=evidence_snapshot(state),
        changed_files=tuple(state.changed_files),
        last_test_status=state.last_test_status,
    )
