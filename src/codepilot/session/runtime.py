from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from codepilot.agent.loop import MinimalAgentLoop, TurnExecutionContext, AgentRunResult
from codepilot.llm.types import CodePilotLLMClient
from codepilot.router import ToolRouter
from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.models import BranchConfirmationRequired, TurnRecord
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore
from codepilot.session.trace_recorder import SessionTraceRecorder


@dataclass(frozen=True)
class TurnExecutionResult:
    """Runtime 对一次 Turn 的执行结果和 Agent 结果的薄封装。"""

    turn: TurnRecord
    result: AgentRunResult


class SessionRuntime:
    """把 Session 持久化生命周期接到现有单线程 AgentLoop。"""

    def __init__(self, database: SessionDatabase, llm: CodePilotLLMClient, router_factory: Callable[[Any], ToolRouter], max_steps: int = 12) -> None:
        self.database = database
        self.store = SessionStore(database)
        self.service = SessionService(database)
        self.assembler = ContextAssembler(database, self.store)
        self.llm = llm
        self.router_factory = router_factory
        self.max_steps = max_steps

    def submit_user_message(self, session_id: str, text: str) -> TurnRecord | BranchConfirmationRequired:
        """提交用户消息；分支变化确认前不写入 Turn。"""

        opened = self.service.open_session(session_id)
        if opened.session.status != "active":
            raise ValueError("archived session is read-only")
        if not opened.project_exists:
            raise FileNotFoundError(opened.project_path)
        if any(turn.status in {"queued", "running", "waiting_permission"} for turn in self.store.list_turns(session_id)):
            raise RuntimeError("session already has a running turn")
        branch = self.service.validate_branch_before_turn(session_id)
        if branch.changed:
            return BranchConfirmationRequired(session_id, branch.expected_branch, branch.actual_branch)
        session = opened.session
        first_user_message = not any(message.role == "user" for message, _ in self.store.list_messages_with_parts(session_id))
        turn = self.store.create_turn(
            session_id=session_id,
            title=f"Turn {len(self.store.list_turns(session_id)) + 1}",
            provider_snapshot=session.provider,
            model_snapshot=session.current_model,
            permission_mode_snapshot=session.permission_mode,
            branch_snapshot=branch.actual_branch,
        )
        self.store.create_message(session_id=session_id, turn_id=turn.turn_id, role="user", status="completed", content=text)
        if session.title == "New session" and first_user_message:
            self.store.update_session(session_id, title=_task_preview(text))
        self.store.append_event(session_id=session_id, event_type="turn_created", payload={"turn_id": turn.turn_id}, turn_id=turn.turn_id)
        self.store.append_event(session_id=session_id, event_type="user_message_created", payload={"text": text}, turn_id=turn.turn_id)
        return self.store.list_turns(session_id)[-1]

    def run_turn(self, turn_id: str, cancellation_token: Any | None = None) -> TurnExecutionResult:
        """从持久化 Turn 组装上下文并执行；终态始终写回 SQLite。"""

        turn = next(turn for session_id in self._session_ids_for_turn(turn_id) for turn in self.store.list_turns(session_id) if turn.turn_id == turn_id)
        session = self.store.get_session(turn.session_id)
        opened = self.service.open_session(session.session_id)
        attempt = self.store.create_attempt(turn_id=turn_id)
        self.store.update_turn_status(turn_id, "running")
        trace = SessionTraceRecorder(self.database, session.session_id, turn_id, attempt.attempt_id)
        router = self.router_factory(trace)
        # Session 模式必须在真实副作用前落 durable execution intent。
        router.lifecycle_observer = trace
        loop = MinimalAgentLoop(llm=self.llm, router=router, trace_logger=trace, max_steps=self.max_steps, cancellation_token=cancellation_token, event_sink=trace)
        task = next(message.content for message, _ in self.store.list_messages_with_parts(session.session_id, turn_id) if message.role == "user")
        context = ContextAssembler(self.database, self.store).build(session.session_id, turn_id, session.provider, session.current_model)
        try:
            result = loop.run_turn(TurnExecutionContext(session.session_id, turn_id, attempt.attempt_id, str(task), opened.project_path, context))
        except Exception:
            self.store.update_attempt_status(attempt.attempt_id, "interrupted")
            self.store.update_turn_status(turn_id, "interrupted")
            raise
        self.store.update_attempt_status(attempt.attempt_id, "completed" if result.success else "failed")
        self.store.update_turn_status(turn_id, "completed" if result.success else "failed")
        return TurnExecutionResult(self.store.list_turns(session.session_id)[-1], result)

    def _session_ids_for_turn(self, turn_id: str) -> list[str]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        if row is None:
            raise LookupError(turn_id)
        return [row[0]]


def _task_preview(text: str) -> str:
    """用第一条用户消息生成短标题。"""

    return " ".join(text.split())[:80] or "New session"
