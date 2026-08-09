from __future__ import annotations

import logging
import os
import socket
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop, TurnExecutionContext
from codepilot.llm.types import CodePilotLLMClient
from codepilot.memory.candidates import MemoryCandidateExtractor
from codepilot.memory.summarizer import LLMSummaryGenerator
from codepilot.memory.turn_window import TurnContextWindow
from codepilot.agent.tool_observation_budget import ToolObservationBudgetPolicy
from codepilot.router import ToolRouter
from codepilot.router.errors import ToolExecutionUncertainError
from codepilot.session.compaction import CompactionService
from codepilot.session.context import ContextAssembler
from codepilot.session.context_recovery import SessionContextRecoveryCoordinator
from codepilot.session.database import SessionDatabase
from codepilot.session.git_context import read_git_context
from codepilot.session.model_capabilities import ModelCapabilities, resolve_model_context_profile
from codepilot.session.models import BranchConfirmationRequired, TurnRecord, TurnSubmission
from codepilot.session.permission import PermissionRequestContext
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore
from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver
from codepilot.session.trace_recorder import SessionTraceRecorder

logger = logging.getLogger(__name__)

_LEASE_RENEW_INTERVAL_SECONDS = 30
_LEASE_RETRY_INTERVAL_SECONDS = 1
_LEASE_MAX_BUSY_RETRIES = 5
_UNCONFIRMED_BRANCH = object()
BLOCKING_TURN_STATUSES = {"queued", "running", "waiting_permission", "recovery_required"}


@dataclass(frozen=True)
class TurnExecutionResult:
    """Runtime 对一次 Turn 的执行结果和 Agent 结果的薄封装。"""

    turn: TurnRecord
    attempt_id: str
    result: AgentRunResult


class SessionRuntime:
    """把 Session 持久化生命周期接到现有单线程 AgentLoop。"""

    def __init__(
        self,
        database: SessionDatabase,
        llm: CodePilotLLMClient,
        router_factory: Callable[[Any], ToolRouter],
        max_steps: int = 12,
        trace_hook: Callable[[Any], None] | None = None,
        capabilities: ModelCapabilities | None = None,
        agent_profile: Any | None = None,
        write_scope: tuple[str, ...] = (),
        runtime_tool_registry_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.database = database
        self.store = SessionStore(database)
        self.service = SessionService(database)
        self.assembler = ContextAssembler(database, self.store)
        self.compaction_service = CompactionService(database, summarizer=LLMSummaryGenerator(llm))
        self.memory_candidates = MemoryCandidateExtractor(database)
        self.llm = llm
        self.router_factory = router_factory
        self.max_steps = max_steps
        self.trace_hook = trace_hook
        self.capabilities = capabilities
        self.agent_profile = agent_profile
        self.write_scope = tuple(write_scope)
        self.runtime_tool_registry_factory = runtime_tool_registry_factory

    def configure_agent_profile(self, profile: Any, *, write_scope: tuple[str, ...] = ()) -> None:
        """Set the immutable child boundary before the next Turn is submitted."""

        self.agent_profile = profile
        self.write_scope = tuple(write_scope)

    def _configure_agent_boundary(self, router: ToolRouter) -> tuple[tuple[Any, ...], str | None]:
        """Bind Profile visibility and write scope to both prompt and execution."""

        if self.agent_profile is None:
            visible = router.list_visible_tool_specs()
            router.configure_allowed_tools(spec.name for spec in visible)
            return visible, None

        # Import lazily: multi_agent.supervisor depends on SessionRuntime's storage types.
        from codepilot.multi_agent.profiles import filter_builtin_specs, filter_scout_mcp_specs
        from codepilot.tools.base import ToolSideEffect
        from codepilot.tools.registry import list_tool_specs

        profile = self.agent_profile
        visible = list(filter_builtin_specs(profile, list_tool_specs()))
        external = router.external_tool_registry
        if profile.allows_mcp and external is not None:
            list_specs = getattr(external, "list_exposed_specs", None)
            if not callable(list_specs):
                list_specs = getattr(external, "list_specs", None)
            if callable(list_specs):
                mcp_specs = tuple(list_specs())
                if profile.name == "scout":
                    mcp_specs = filter_scout_mcp_specs(mcp_specs)
                visible.extend(mcp_specs)

        if profile.name == "general" and not self.write_scope:
            visible = [spec for spec in visible if spec.side_effect != ToolSideEffect.LOCAL_WRITE]

        router.configure_allowed_tools(
            (spec.name for spec in visible),
            write_scope=self.write_scope if profile.name == "general" else None,
        )
        return tuple(visible), profile.instructions

    def submit_user_message(
        self,
        session_id: str,
        text: str,
        *,
        confirmed_branch: str | None | object = _UNCONFIRMED_BRANCH,
    ) -> TurnSubmission | BranchConfirmationRequired:
        """提交用户消息；分支变化确认前不写入任何 Turn 业务记录。

        用户确认后仍会重新读取 Git 的实际分支。只有确认值与本次读取值一致时，Store
        才会在单个事务中创建 Turn、User Message、Attempt 和对应事件。
        """

        opened = self.service.open_session(session_id)
        if opened.session.status != "active":
            raise ValueError("archived session is read-only")
        if not opened.project_exists:
            raise FileNotFoundError(opened.project_path)
        if any(turn.status in BLOCKING_TURN_STATUSES for turn in self.store.list_turns(session_id)):
            raise RuntimeError("session already has a running turn")
        branch = self.service.validate_branch_before_turn(session_id)
        branch_confirmation_provided = confirmed_branch is not _UNCONFIRMED_BRANCH
        if branch.changed and not branch_confirmation_provided:
            return BranchConfirmationRequired(session_id, branch.expected_branch, branch.actual_branch)
        return self.store.create_turn_submission(
            session_id=session_id,
            text=text,
            actual_branch_reader=lambda: read_git_context(opened.project_path).branch,
            confirmed_branch=confirmed_branch if isinstance(confirmed_branch, str) or confirmed_branch is None else None,
            branch_confirmation_provided=branch_confirmation_provided,
        )

    def run_turn(self, turn_id: str, attempt_id: str, cancellation_token: Any | None = None) -> TurnExecutionResult:
        """从持久化 Turn 组装上下文并执行；终态始终写回 SQLite。"""

        turn = self.store.get_turn(turn_id)
        session = self.store.get_session(turn.session_id)
        opened = self.service.open_session(session.session_id)
        attempt = self.store.get_attempt(attempt_id)
        if attempt.turn_id != turn_id:
            raise ValueError("attempt does not belong to turn")
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
        self.store.start_turn_attempt(
            turn_id,
            attempt_id,
            worker_id=worker_id,
            lease_expires_at=_lease_expiry(),
        )
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=lambda: _renew_lease_until_stopped(self.store, attempt_id, worker_id, heartbeat_stop, lease_lost),
            daemon=True,
        )
        heartbeat.start()
        try:
            trace = SessionTraceRecorder(self.database, session.session_id, turn_id, attempt.attempt_id, record_hook=self.trace_hook)
            router = self.router_factory(trace)
            if hasattr(router, "policy_context"):
                router.policy_context = router.policy_context.model_copy(update={"repo": opened.project_path})
            if self.runtime_tool_registry_factory is not None:
                if router.runtime_tool_registry is not None:
                    raise ValueError("runtime tool registry is already configured")
                from codepilot.multi_agent.runtime_tools import AgentControlContext

                router.runtime_tool_registry = self.runtime_tool_registry_factory(
                    AgentControlContext(
                        parent_session_id=session.session_id,
                        parent_turn_id=turn_id,
                        parent_attempt_id=attempt.attempt_id,
                        parent_repo=opened.project_path,
                    )
                )
            visible_tool_specs, agent_instructions = self._configure_agent_boundary(router)
            if hasattr(router, "permission_request_context"):
                router.permission_request_context = PermissionRequestContext(session.session_id, turn_id, attempt.attempt_id, None)
            # Trace 只记录事件；业务表由稳定 ID 的 Lifecycle Observer 单独维护。
            router.lifecycle_observer = SQLiteToolLifecycleObserver(self.database, session.session_id, turn_id, attempt_id, trace)
            user_message = self.store.get_user_message_for_turn(turn_id)
            if user_message is None:
                raise LookupError(turn_id)
            profile = resolve_model_context_profile(turn.provider_snapshot, turn.model_snapshot, self.capabilities)
            loop = MinimalAgentLoop(
                llm=self.llm,
                router=router,
                trace_logger=trace,
                max_steps=self.max_steps,
                visible_tool_specs=visible_tool_specs,
                cancellation_token=_LeaseAwareCancellationToken(cancellation_token, lease_lost),
                event_sink=trace,
                context_window=TurnContextWindow(self.database, profile),
                tool_observation_budget_policy=ToolObservationBudgetPolicy(),
                model_context_profile=profile,
                context_recovery=SessionContextRecoveryCoordinator(
                    self.database,
                    self.compaction_service,
                    self.assembler,
                    profile,
                ),
            )
            self.store.update_turn_metadata(
                turn_id,
                {
                    "max_input_tokens": profile.max_input_tokens,
                    "max_output_tokens": profile.max_output_tokens,
                    "reasoning_format": profile.reasoning_format,
                    "capability_source": profile.capability_source,
                },
            )
            if profile.capability_source == "conservative_unknown_model" and not any(
                event.event_type == "model_capability_fallback" and event.turn_id == turn_id
                for event in self.store.list_events(session.session_id)
            ):
                self.store.append_event(
                    session_id=session.session_id,
                    event_type="model_capability_fallback",
                    payload={"provider": profile.provider, "model": profile.model, "max_input_tokens": profile.max_input_tokens},
                    turn_id=turn_id,
                    metadata={"source": profile.capability_source},
                )
            try:
                self.compaction_service.ensure_context_budget(session.session_id, turn_id, profile)
                context = self.assembler.build_with_manifest(
                    session.session_id,
                    turn_id,
                    turn.provider_snapshot,
                    turn.model_snapshot,
                    profile=profile,
                    tool_specs=visible_tool_specs,
                    agent_instructions=agent_instructions,
                )
            except Exception:
                self.store.interrupt_turn_attempt(turn_id, attempt_id, "context compaction failed", worker_id=worker_id)
                self.store.update_turn_status(turn_id, "recovery_required")
                raise
            result = loop.run_turn(
                TurnExecutionContext(
                    session.session_id,
                    turn_id,
                    attempt.attempt_id,
                    str(user_message.content),
                    opened.project_path,
                    context.messages,
                    context,
                )
            )
        except ToolExecutionUncertainError as exc:
            heartbeat_stop.set()
            heartbeat.join()
            self.store.require_tool_recovery(
                turn_id,
                attempt_id,
                exc.tool_call_id,
                str(exc),
                worker_id,
            )
            raise
        except Exception as exc:
            heartbeat_stop.set()
            heartbeat.join()
            if self.store.get_turn(turn_id).status == "running":
                self.store.interrupt_turn_attempt(turn_id, attempt_id, str(exc), worker_id=worker_id)
            raise
        heartbeat_stop.set()
        heartbeat.join()
        if result.status in {"success", "message_complete"}:
            self.store.finish_turn_attempt(turn_id, attempt_id, attempt_status="completed", turn_status="completed", worker_id=worker_id)
            memory_write = session.metadata.get(
                "memory_write",
                getattr(self.agent_profile, "memory_write", True),
            )
            if memory_write is not False:
                self._extract_memory_candidates_best_effort(
                    session_id=session.session_id,
                    turn_id=turn_id,
                    attempt_id=attempt_id,
                )
        elif result.status == "cancelled":
            self.store.finish_turn_attempt(turn_id, attempt_id, attempt_status="cancelled", turn_status="cancelled", worker_id=worker_id)
        else:
            self.store.finish_turn_attempt(turn_id, attempt_id, attempt_status="failed", turn_status="failed", worker_id=worker_id)
        return TurnExecutionResult(self.store.get_turn(turn_id), attempt_id, result)

    def _extract_memory_candidates_best_effort(
        self,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str,
    ) -> None:
        try:
            candidates = self.memory_candidates.extract(session_id, turn_id)
        except Exception as exc:
            logger.exception(
                "Memory candidate extraction failed",
                extra={"session_id": session_id, "turn_id": turn_id, "attempt_id": attempt_id},
            )
            self._append_memory_event_best_effort(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                event_type="memory_candidate_extraction_failed",
                payload={"error": str(exc)},
            )
            return
        self._append_memory_event_best_effort(
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            event_type="memory_candidates_extracted",
            payload={
                "candidate_ids": [candidate.candidate_id for candidate in candidates],
                "count": len(candidates),
            },
        )

    def _append_memory_event_best_effort(
        self,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            self.store.append_event(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                event_type=event_type,
                payload=payload,
                metadata={"source": "session_runtime"},
            )
        except Exception:
            logger.exception(
                "Memory candidate event persistence failed",
                extra={"session_id": session_id, "turn_id": turn_id, "attempt_id": attempt_id, "event_type": event_type},
            )


def _lease_expiry() -> str:
    return (datetime.now(UTC) + timedelta(minutes=2)).isoformat()


class _LeaseAwareCancellationToken:
    def __init__(self, external: Any | None, lease_lost: threading.Event) -> None:
        self.external = external
        self.lease_lost = lease_lost

    def is_cancelled(self) -> bool:
        return self.lease_lost.is_set() or bool(self.external and self.external.is_cancelled())


def _renew_lease_until_stopped(
    store: SessionStore,
    attempt_id: str,
    worker_id: str,
    stop: threading.Event,
    lease_lost: threading.Event,
) -> None:
    while not stop.wait(_LEASE_RENEW_INTERVAL_SECONDS):
        for retry_index in range(_LEASE_MAX_BUSY_RETRIES + 1):
            if stop.is_set():
                return
            try:
                store.renew_attempt_lease(attempt_id, worker_id, _lease_expiry())
                break
            except RuntimeError:
                lease_lost.set()
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                transient = "locked" in message or "busy" in message
                if not transient:
                    logger.exception(
                        "Session lease renewal failed with non-transient SQLite error",
                        extra={"attempt_id": attempt_id},
                    )
                    lease_lost.set()
                    return
                if retry_index >= _LEASE_MAX_BUSY_RETRIES:
                    logger.error(
                        "Session lease renewal exhausted SQLite busy retries",
                        extra={"attempt_id": attempt_id},
                    )
                    lease_lost.set()
                    return
                if stop.wait(_LEASE_RETRY_INTERVAL_SECONDS):
                    return
            except Exception:
                logger.exception(
                    "Unexpected session lease renewal failure",
                    extra={"attempt_id": attempt_id},
                )
                lease_lost.set()
                return
