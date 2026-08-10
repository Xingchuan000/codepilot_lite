from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from codepilot.agent.actions import AgentFinishAction, AgentFinishArgs, agent_action_to_trace_input
from codepilot.agent.evidence import AssistantStopReason, CompletionKind, EvidenceDecision
from codepilot.agent.internal_tools import CODEPILOT_FINISH_TOOL_NAME, CODEPILOT_FINISH_TOOL_SPEC
from codepilot.agent.observation import (
    format_finish_blocked_observation,
    format_finish_required_observation,
    format_pruned_observation,
)
from codepilot.agent.outcome import RunOutcomeSnapshot, build_run_outcome
from codepilot.agent.prompts import build_initial_messages
from codepilot.agent.state import (
    AgentState,
    create_initial_state,
    evidence_snapshot,
    mark_finished_from_action,
    refresh_evidence_state,
    register_finish_claim,
    register_tool_attempt,
    update_state_from_route_result,
)
from codepilot.agent.tool_observation_budget import ToolObservationBudgetPolicy
from codepilot.agent.tool_output_pruner import ToolOutputPruner
from codepilot.llm.errors import LLMContextOverflowError, normalize_llm_exception
from codepilot.llm.fake import FakeLLMExhaustedError
from codepilot.llm.types import (
    ChatMessage,
    ChatMessagePart,
    CodePilotLLMClient,
    LLMReasoningReplay,
    LLMResponse,
    LLMToolCall,
    RichChatMessage,
)
from codepilot.memory.policy import redact_memory_value
from codepilot.memory.turn_window import ContextPreparationResult
from codepilot.router import ToolRouter
from codepilot.router.errors import ToolExecutionUncertainError, ToolPreExecutionError
from codepilot.session.context_adapters import PreparedContext
from codepilot.session.context_budget import estimate_tokens
from codepilot.session.context_recovery import ContextRecoveryResult
from codepilot.session.model_context import ModelContextProfile
from codepilot.tools.actions import ToolAction
from codepilot.tools.base import ToolSpec
from codepilot.tools.registry import list_tool_specs
from codepilot.trace.protocol import TraceRecorder


@dataclass(frozen=True)
class AgentRunResult:
    """MinimalAgentLoop 对外暴露的结果。

    最终状态和证据只存放在 outcome 中。下方只读 property 保留原有公开访问方式，
    使现有调用方可以逐步迁移，但不会在结果对象里保存第二份可变证据数据。
    """

    success: bool
    status: str
    summary: str
    steps: int
    outcome: RunOutcomeSnapshot
    task_intent: str = "general"
    trace_path: str | None = None
    error: str | None = None
    policy_violations: int = 0

    @property
    def completion_kind(self) -> CompletionKind | None:
        return self.outcome.completion_kind

    @property
    def assistant_stop_reason(self) -> AssistantStopReason | None:
        return self.outcome.assistant_stop_reason

    @property
    def delivery_kind(self) -> str | None:
        return self.outcome.delivery_kind

    @property
    def requires_evidence(self) -> bool:
        return self.outcome.evidence.requires_evidence

    @property
    def evidence_reasons(self) -> list[str]:
        return list(self.outcome.evidence.reasons)

    @property
    def write_attempted(self) -> bool:
        return self.outcome.evidence.write_attempted

    @property
    def write_executed(self) -> bool:
        return self.outcome.evidence.write_executed

    @property
    def written_files(self) -> list[str]:
        return list(self.outcome.evidence.written_files)

    @property
    def observed_changed_files(self) -> list[str]:
        return list(self.outcome.evidence.observed_changed_files)

    @property
    def claimed_changed_files(self) -> list[str]:
        return list(self.outcome.evidence.claimed_changed_files)

    @property
    def tests_required(self) -> bool:
        return self.outcome.evidence.tests_required

    @property
    def diff_required(self) -> bool:
        return self.outcome.evidence.diff_required

    @property
    def diff_checked(self) -> bool:
        return self.outcome.evidence.diff_checked

    @property
    def missing_evidence(self) -> list[str]:
        return list(self.outcome.evidence.missing)

    @property
    def changed_files(self) -> list[str]:
        return list(self.outcome.changed_files)

    @property
    def last_test_status(self) -> str | None:
        return self.outcome.last_test_status


@dataclass(frozen=True)
class TurnExecutionContext:
    """一次 Session Turn 的完整执行输入；历史由调用方预先从 SQLite 组装。"""

    session_id: str | None
    turn_id: str | None
    attempt_id: str | None
    task: str
    repo: Path
    messages: list[ChatMessage | RichChatMessage]
    prepared_context: PreparedContext | None = None


class AgentEventSink(Protocol):
    """Loop 向 Session 持久化层发布的最小语义事件接口。"""

    def assistant_message_started(self, **kwargs: Any) -> None: ...
    def assistant_text_delta(self, **kwargs: Any) -> None: ...
    def assistant_message_completed(self, **kwargs: Any) -> None: ...
    def tool_call_created(self, **kwargs: Any) -> None: ...
    def tool_result_created(self, **kwargs: Any) -> None: ...
    def record_native_tool_call(self, **kwargs: Any) -> None: ...
    def loop_observation_created(self, **kwargs: Any) -> None: ...
    def agent_finished(self, **kwargs: Any) -> None: ...

    def assistant_message_interrupted(self, **kwargs: Any) -> None: ...


class AgentContextWindow(Protocol):
    def prepare_for_llm(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        attempt_id: str | None,
        step: int,
        messages: list[ChatMessage | RichChatMessage],
        base_message_count: int,
        task: str,
        evidence: dict[str, Any],
    ) -> ContextPreparationResult: ...


class AgentContextRecovery(Protocol):
    def recover_from_provider_overflow(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        attempt_id: str | None,
        step: int,
        task: str,
        evidence: dict[str, Any],
        original_messages: list[ChatMessage | RichChatMessage],
        original_base_message_count: int,
        error: LLMContextOverflowError,
    ) -> ContextRecoveryResult: ...

    def retry_exhausted(self, **kwargs: Any) -> None: ...


def _inject_repo_if_required(
    arguments: dict[str, Any] | None,
    repo: Path,
    spec: ToolSpec | None,
) -> dict[str, Any]:
    """确保模型不能借 repo 参数切换到当前仓库之外。"""

    injected = dict(arguments or {})
    if spec is None or not spec.inject_repo:
        return injected
    if "repo" not in injected:
        injected["repo"] = str(repo)
        return injected
    repo_value = injected["repo"]
    if Path(repo_value).expanduser().resolve() != repo.resolve():
        raise ValueError("repo argument must match the current repository")
    injected["repo"] = str(repo)
    return injected


def _safe_error(exc: BaseException) -> str:
    return str(redact_memory_value(str(exc)).value)


def _checkpoint_evidence(state: AgentState) -> dict[str, Any]:
    return {
        **evidence_snapshot(state).to_payload(),
        "last_test_status": state.last_test_status,
        "last_test_command": state.last_test_command,
        "last_failed_tests": state.last_failed_tests,
    }


def _infer_finish_delivery_kind(state: AgentState, action: AgentFinishAction) -> str:
    """根据模型声明和真实写入轨迹，推导这次 finish 到底是在做消息回复还是代码交付。"""

    has_write_trace = state.write_attempted or state.write_executed or bool(state.written_files)
    claims_code_change = bool(action.changed_files)
    if action.delivery_kind == "code_change":
        return "code_change"
    if has_write_trace or claims_code_change:
        return "code_change"
    if action.delivery_kind in {"message", "analysis"}:
        return action.delivery_kind
    return "message"


@dataclass(frozen=True)
class FinishResolution:
    """结构化 finish 的纯决策结果，不执行 Trace 或状态修改。"""

    status: str
    completion_kind: CompletionKind
    success: bool
    status_normalized: bool
    blocked_by_evidence: bool = False


def _resolve_finish(
    action: AgentFinishAction,
    *,
    delivery_kind: str,
    evidence: EvidenceDecision,
) -> FinishResolution:
    """按照既有顺序解析 finish，不读取或修改 AgentState。"""

    if action.status == "failed":
        return FinishResolution("failed", "task_failed", False, False)
    if action.status == "partial":
        return FinishResolution("partial", "task_partial", False, False)
    if evidence.missing:
        return FinishResolution("success", "task_success", False, False, blocked_by_evidence=True)
    if delivery_kind != "code_change":
        return FinishResolution("message_complete", "message_complete", True, True)
    return FinishResolution("success", "task_success", True, False)


class MinimalAgentLoop:
    """按模型原生 tool_calls 执行一次工具闭环。"""

    def __init__(
        self,
        *,
        llm: CodePilotLLMClient,
        router: ToolRouter,
        trace_logger: TraceRecorder | None = None,
        max_steps: int = 12,
        prompt_extra_tool_specs: Sequence[ToolSpec] | None = None,
        visible_tool_specs: Sequence[ToolSpec] | None = None,
        cancellation_token: Any | None = None,
        event_sink: AgentEventSink | None = None,
        context_window: AgentContextWindow | None = None,
        tool_output_pruner: ToolOutputPruner | None = None,
        tool_observation_token_budget: int | None = None,
        tool_observation_budget_policy: ToolObservationBudgetPolicy | None = None,
        model_context_profile: ModelContextProfile | None = None,
        context_recovery: AgentContextRecovery | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than 0")
        # AgentLoop 和 ToolRouter 必须共用同一个 TraceLogger，
        # 否则一轮运行里的 agent 事件和 tool 事件会被拆到两份 trace 中。
        if trace_logger is not None and trace_logger is not router.trace_logger:
            raise ValueError(
                "MinimalAgentLoop must use the same TraceLogger as ToolRouter. "
                "Pass the logger into ToolRouter instead."
            )
        self.llm = llm
        self.router = router
        self.max_steps = max_steps
        self.trace_logger = router.trace_logger
        self.prompt_extra_tool_specs = list(prompt_extra_tool_specs or [])
        self.visible_tool_specs = list(visible_tool_specs) if visible_tool_specs is not None else None
        self.cancellation_token = cancellation_token
        self.event_sink = event_sink
        self.context_window = context_window
        self.tool_output_pruner = tool_output_pruner or ToolOutputPruner()
        self.tool_observation_token_budget = tool_observation_token_budget
        self.tool_observation_budget_policy = tool_observation_budget_policy
        self.model_context_profile = model_context_profile
        self.context_recovery = context_recovery
        self.tool_specs_by_name = {}
        base_specs = self.visible_tool_specs if self.visible_tool_specs is not None else list_tool_specs()
        for spec in base_specs:
            self.tool_specs_by_name[spec.name] = spec
        for spec in self.prompt_extra_tool_specs:
            existing = self.tool_specs_by_name.get(spec.name)
            if existing is not None and existing != spec:
                raise ValueError(f"Duplicate tool spec: {spec.name}")
            self.tool_specs_by_name[spec.name] = spec
        if visible_tool_specs is not None or self.prompt_extra_tool_specs:
            self.router.configure_allowed_tools(self.tool_specs_by_name)

    def _cancel_requested(self) -> bool:
        return bool(self.cancellation_token and self.cancellation_token.is_cancelled())

    def _llm_tool_specs(self) -> tuple[ToolSpec, ...]:
        return (*self.tool_specs_by_name.values(), CODEPILOT_FINISH_TOOL_SPEC)

    def _complete_llm(self, messages: list[ChatMessage | RichChatMessage], context: TurnExecutionContext) -> LLMResponse:
        """Call the LLM with the Native tool contract."""
        if self.event_sink is not None:
            self.event_sink.assistant_message_started(turn_id=context.turn_id, attempt_id=context.attempt_id, streaming=False)
        try:
            return self.llm.complete(messages, tools=self._llm_tool_specs(), tool_choice="auto")  # type: ignore[arg-type]
        except FakeLLMExhaustedError:
            raise
        except Exception as exc:
            raise normalize_llm_exception(exc, output_started=False) from exc

    def _cancelled_result(self, state: AgentState) -> AgentRunResult:
        """在一个位置完成取消状态、Trace 和返回值构造。"""

        state.final_status = "cancelled"
        state.final_summary = "cancelled"
        state.assistant_stop_reason = "cancelled"
        state.completion_kind = "cancelled"
        self.trace_logger.record_run_cancelled(
            metadata={
                "source": "minimal_agent_loop",
                **build_run_outcome(state, status="cancelled").to_payload(),
            }
        )
        return self._result(state=state, status="cancelled", summary="cancelled", success=False, error="cancelled")

    def _result(
        self,
        *,
        state: AgentState,
        status: str,
        summary: str,
        success: bool,
        error: str | None = None,
    ) -> AgentRunResult:
        """通过统一 Outcome 快照构造返回值，不再复制各个证据字段。"""

        return AgentRunResult(
            success=success,
            status=status,
            summary=summary,
            steps=state.step,
            outcome=build_run_outcome(state, status=status),
            task_intent=state.task_intent,
            trace_path=str(self.trace_logger.trace_path) if self.trace_logger.trace_path is not None else None,
            error=error,
            policy_violations=state.policy_violations,
        )

    def _tool_side_effect(self, tool_name: str) -> str | None:
        spec = self.tool_specs_by_name.get(tool_name)
        return spec.side_effect.value if spec is not None else None

    def _runtime_failure_result(
        self,
        state: AgentState,
        *,
        status: str,
        stop_reason: AssistantStopReason,
        error: str | None = None,
    ) -> AgentRunResult:
        """统一收尾 LLM 耗尽、LLM 调用失败和最大步数耗尽。"""

        state.final_status = status
        state.final_summary = status
        state.assistant_stop_reason = stop_reason
        state.completion_kind = "runtime_failure"
        metadata = build_run_outcome(state, status=status).to_payload()
        if error is not None:
            metadata["error"] = error
        self.trace_logger.record_run_end(success=False, summary=status, metadata=metadata)
        return self._result(state=state, status=status, summary=status, success=False, error=error)

    def _natural_reply_result(
        self,
        state: AgentState,
        *,
        response_content: str,
        text: str,
    ) -> AgentRunResult:
        """保持自然文本结束的现有状态和 Trace 行为。"""

        state.messages.append(ChatMessage(role="assistant", content=response_content))
        state.assistant_stop_reason = "natural_reply"
        decision = refresh_evidence_state(state)
        status = "message_complete" if not decision.requires_evidence else "task_incomplete"
        completion_kind: CompletionKind = "message_complete" if not decision.requires_evidence else "task_incomplete"
        success = not decision.requires_evidence
        state.final_status = status
        state.final_summary = text
        state.completion_kind = completion_kind
        self.trace_logger.record_agent_finish(
            status=status,
            success=success,
            summary=text,
            metadata={
                "requested_status": None,
                "effective_status": status,
                "status_normalized": False,
                "completion_kind": completion_kind,
                "assistant_stop_reason": state.assistant_stop_reason,
                **evidence_snapshot(state).to_payload(),
                "changed_files": list(state.changed_files),
            },
        )
        self.trace_logger.record_run_end(
            success=success,
            summary=text,
            metadata={
                "status": status,
                "completion_kind": completion_kind,
                "assistant_stop_reason": state.assistant_stop_reason,
                **evidence_snapshot(state).to_payload(),
            },
        )
        return self._result(state=state, status=status, summary=text, success=success)

    def _finish_metadata(
        self,
        *,
        state: AgentState,
        action: AgentFinishAction,
        resolution: FinishResolution,
        delivery_kind: str,
    ) -> dict[str, Any]:
        """构造 agent_finish 的唯一元数据，保留现有诊断字段。"""

        return {
            "requested_status": action.status,
            "effective_status": resolution.status,
            "status_normalized": resolution.status_normalized,
            "completion_kind": resolution.completion_kind,
            "assistant_stop_reason": state.assistant_stop_reason,
            "delivery_kind": delivery_kind,
            "tests": action.tests,
            **evidence_snapshot(state).to_payload(),
            "changed_files": list(state.changed_files),
        }

    def _finish_from_action(
        self,
        *,
        state: AgentState,
        action: AgentFinishAction,
        resolution: FinishResolution,
        delivery_kind: str,
        provider_tool_call_id: str | None = None,
    ) -> AgentRunResult:
        """统一完成结构化 finish 的动作、状态、Trace 和结果收尾。"""

        self.trace_logger.record_agent_action(
            action_type=action.type,
            tool_name=CODEPILOT_FINISH_TOOL_NAME,
            input=agent_action_to_trace_input(action),
            success=True,
            metadata={
                **({"provider_tool_call_id": provider_tool_call_id} if provider_tool_call_id else {}),
                "requested_status": action.status,
                "effective_status": resolution.status,
                "status_normalized": resolution.status_normalized,
                "completion_kind": resolution.completion_kind,
                "assistant_stop_reason": "structured_finish",
                "delivery_kind": delivery_kind,
                **evidence_snapshot(state).to_payload(),
            },
        )
        mark_finished_from_action(
            state,
            action,
            effective_status=resolution.status,
            completion_kind=resolution.completion_kind,
            delivery_kind=delivery_kind,
        )
        self.trace_logger.record_agent_finish(
            status=resolution.status,
            success=resolution.success,
            summary=action.summary,
            metadata=self._finish_metadata(
                state=state,
                action=action,
                resolution=resolution,
                delivery_kind=delivery_kind,
            ),
        )
        self.trace_logger.record_run_end(
            success=resolution.success,
            summary=action.summary,
            metadata={
                "status": resolution.status,
                "completion_kind": resolution.completion_kind,
                "assistant_stop_reason": state.assistant_stop_reason,
                "delivery_kind": delivery_kind,
                **evidence_snapshot(state).to_payload(),
            },
        )
        return self._result(
            state=state,
            status=resolution.status,
            summary=action.summary,
            success=resolution.success,
        )

    def _append_native_tool_result(
        self,
        *,
        state: AgentState,
        call: LLMToolCall,
        content: str,
        tool_call_id: str | None = None,
        success: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        state.messages.append(
            RichChatMessage(
                role="tool",
                parts=(
                    ChatMessagePart(
                        type="tool_result",
                        content={
                            "provider_tool_call_id": call.provider_tool_call_id,
                            "tool_name": call.name,
                            "content": content,
                            **({"codepilot_tool_call_id": tool_call_id} if tool_call_id is not None else {}),
                        },
                    ),
                ),
            )
        )
        if self.event_sink is not None:
            self.event_sink.tool_result_created(
                tool_name=call.name,
                success=success,
                content=content,
                provider_tool_call_id=call.provider_tool_call_id,
                tool_call_id=tool_call_id,
                turn_id=self._context_turn_id,
                attempt_id=self._context_attempt_id,
                **(metadata or {}),
            )
        self.trace_logger.record_agent_observation(
            tool_name=call.name,
            observation=content,
            metadata={"provider_tool_call_id": call.provider_tool_call_id, "tool_call_id": tool_call_id, **(metadata or {})},
        )

    def _handle_finish_tool_call(
        self,
        *,
        state: AgentState,
        context: TurnExecutionContext,
        call: LLMToolCall,
    ) -> AgentRunResult | None:
        try:
            args = AgentFinishArgs.model_validate(call.arguments)
        except ValidationError as exc:
            self._append_native_tool_result(
                state=state,
                call=call,
                content=f"Validation error for {CODEPILOT_FINISH_TOOL_NAME}: {exc}",
            )
            return None

        action = AgentFinishAction(**args.model_dump())
        register_finish_claim(state, action)
        delivery_kind = _infer_finish_delivery_kind(state, action)
        if delivery_kind == "code_change":
            state.task_requires_code_delivery = True
        decision = refresh_evidence_state(state)
        resolution = _resolve_finish(action, delivery_kind=delivery_kind, evidence=decision)
        if resolution.blocked_by_evidence:
            if delivery_kind == "code_change":
                state.delivery_kind = "code_change"
            self.trace_logger.record_agent_action(
                action_type=action.type,
                tool_name=call.name,
                input=agent_action_to_trace_input(action),
                success=False,
                error="finish success blocked by evidence gate",
                metadata={
                    "provider_tool_call_id": call.provider_tool_call_id,
                    "finish_blocked_by_evidence": True,
                    "requested_status": action.status,
                    "delivery_kind": delivery_kind,
                    **evidence_snapshot(state).to_payload(),
                },
            )
            self._append_native_tool_result(
                state=state,
                call=call,
                content=format_finish_blocked_observation(
                    missing_evidence=list(state.missing_evidence),
                    last_test_status=state.last_test_status,
                    last_test_command=state.last_test_command,
                    diff_checked=state.diff_checked,
                    written_files=list(state.written_files),
                ),
                success=False,
                metadata={"finish_blocked_by_evidence": True},
            )
            return None
        return self._finish_from_action(
            state=state,
            action=action,
            resolution=resolution,
            delivery_kind=delivery_kind,
            provider_tool_call_id=call.provider_tool_call_id,
        )

    def _execute_native_tool_call(
        self,
        *,
        state: AgentState,
        context: TurnExecutionContext,
        call: LLMToolCall,
    ) -> None:
        try:
            injected_args = _inject_repo_if_required(
                call.arguments,
                state.repo,
                self.tool_specs_by_name.get(call.name),
            )
            register_tool_attempt(
                state,
                tool_name=call.name,
                side_effect=self._tool_side_effect(call.name),
                arguments=injected_args,
            )
        except Exception as exc:
            self._append_native_tool_result(
                state=state,
                call=call,
                content=f"Tool preparation error: {exc}",
            )
            return

        refresh_evidence_state(state)
        tool_action = ToolAction(
            tool_name=call.name,
            arguments=injected_args,
            reason=None,
            metadata={"provider_tool_call_id": call.provider_tool_call_id},
        )
        if self.event_sink is not None:
            self.event_sink.tool_call_created(
                tool_name=call.name,
                arguments=injected_args,
                provider_tool_call_id=call.provider_tool_call_id,
                turn_id=context.turn_id,
                attempt_id=context.attempt_id,
            )
        if self._cancel_requested():
            return
        try:
            route_result = self.router.route(tool_action)
        except ToolPreExecutionError as exc:
            if self._cancel_requested():
                return
            self._append_native_tool_result(
                state=state,
                call=call,
                content=f"Tool execution error: {exc}",
                tool_call_id=exc.tool_call_id,
            )
            return
        except ToolExecutionUncertainError:
            raise

        token_budget = self.tool_observation_token_budget
        if token_budget is None:
            if self.tool_observation_budget_policy is None or self.model_context_profile is None:
                token_budget = 2000
            else:
                token_budget = self.tool_observation_budget_policy.resolve(
                    tool_name=route_result.tool_name,
                    profile=self.model_context_profile,
                    estimated_result_tokens=estimate_tokens(route_result.result.output or route_result.result.error or ""),
                ).token_limit
        pruned = self.tool_output_pruner.prune(route_result, token_budget=token_budget)
        observation = format_pruned_observation(route_result, pruned)
        update_state_from_route_result(state, route_result)
        refresh_evidence_state(state)
        if self._cancel_requested():
            return
        self._append_native_tool_result(
            state=state,
            call=call,
            content=observation,
            tool_call_id=route_result.metadata.get("tool_call_id"),
            success=route_result.success,
            metadata={
                "prune_metadata": {
                    "pruned": pruned.truncated,
                    "prune_strategy": pruned.strategy,
                    "original_chars": pruned.original_chars,
                    "retained_chars": pruned.retained_chars,
                    "transformed": pruned.transformed,
                    "length_truncated": pruned.length_truncated,
                },
            },
        )

    def _handle_tool_calls(
        self,
        *,
        state: AgentState,
        context: TurnExecutionContext,
        response: LLMResponse,
    ) -> AgentRunResult | None:
        assistant_parts: list[ChatMessagePart] = []
        if response.reasoning_replay is not None:
            assistant_parts.append(
                ChatMessagePart(
                    type="reasoning_replay",
                    content={"blocks": list(response.reasoning_replay.blocks)},
                    provider_format=response.reasoning_replay.provider_format,
                    replayable=True,
                )
            )
        if response.content:
            assistant_parts.append(ChatMessagePart(type="text", content=response.content))
        for call in response.tool_calls:
            assistant_parts.append(
                ChatMessagePart(
                    type="tool_call",
                    content={
                        "provider_tool_call_id": call.provider_tool_call_id,
                        "tool_name": call.name,
                        "arguments": call.arguments,
                    },
                )
            )
        state.messages.append(RichChatMessage(role="assistant", parts=tuple(assistant_parts)))
        if self.event_sink is not None:
            for call in response.tool_calls:
                self.event_sink.record_native_tool_call(
                    provider_tool_call_id=call.provider_tool_call_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
        for call in response.tool_calls:
            if call.name == CODEPILOT_FINISH_TOOL_NAME:
                result = self._handle_finish_tool_call(state=state, context=context, call=call)
                if result is not None:
                    return result
                continue
            self._execute_native_tool_call(state=state, context=context, call=call)
        return None

    def _handle_natural_reply(
        self,
        state: AgentState,
        *,
        response_content: str,
        reasoning_replay: LLMReasoningReplay | None = None,
    ) -> AgentRunResult | None:
        decision = refresh_evidence_state(state)
        if decision.requires_evidence:
            if reasoning_replay is None:
                state.messages.append(ChatMessage(role="assistant", content=response_content))
            else:
                state.messages.append(
                    RichChatMessage(
                        role="assistant",
                        parts=(
                            ChatMessagePart(
                                type="reasoning_replay",
                                content={"blocks": list(reasoning_replay.blocks)},
                                provider_format=reasoning_replay.provider_format,
                                replayable=True,
                            ),
                            ChatMessagePart(type="text", content=response_content),
                        ),
                    )
                )
            observation = format_finish_required_observation()
            state.messages.append(ChatMessage(role="user", content=observation))
            if self.event_sink is not None:
                self.event_sink.loop_observation_created(
                    content=observation,
                    category="finish_required",
                    turn_id=self._context_turn_id,
                    attempt_id=self._context_attempt_id,
                )
            self.trace_logger.record_agent_observation(
                tool_name=CODEPILOT_FINISH_TOOL_NAME,
                observation=observation,
                metadata={"finish_required": True, **evidence_snapshot(state).to_payload()},
            )
            return None
        return self._natural_reply_result(state, response_content=response_content, text=response_content)

    def run_turn(self, context: TurnExecutionContext) -> AgentRunResult:
        """执行一个 Turn；不会重新生成或查询历史消息。"""

        self._context_turn_id = context.turn_id
        self._context_attempt_id = context.attempt_id
        state = create_initial_state(
            context.task,
            context.repo,
            max_steps=self.max_steps,
            messages=context.messages,
            base_context_items=context.prepared_context.selected_items if context.prepared_context is not None else (),
            omitted_context_items=context.prepared_context.omitted_items if context.prepared_context is not None else (),
        )
        initial_evidence = evidence_snapshot(state)
        self.trace_logger.record_run_start(
            task=context.task,
            metadata={
                "source": "minimal_agent_loop",
                "repo": str(state.repo),
                "max_steps": self.max_steps,
                "task_intent": state.task_intent,
                "task_requires_code_delivery": state.task_requires_code_delivery,
                "requires_evidence": initial_evidence.requires_evidence,
                "initial_evidence_reasons": list(initial_evidence.reasons),
            },
        )
        try:
            while state.step < state.max_steps and not state.finished:
                # 每轮开始先检查取消，避免任务已取消后仍调用一次模型。
                if self._cancel_requested():
                    return self._cancelled_result(state)
                state.step += 1
                if self.context_window is not None:
                    preparation = self.context_window.prepare_for_llm(
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        attempt_id=context.attempt_id,
                        step=state.step,
                        messages=state.messages,
                        base_message_count=state.base_message_count,
                        task=state.task,
                        evidence=_checkpoint_evidence(state),
                        selected_context_items=state.base_context_items,
                        omitted_context_items=state.omitted_context_items,
                    )
                    state.messages = preparation.messages
                    state.base_message_count = preparation.base_message_count
                    state.base_context_items = preparation.selected_context_items
                    state.omitted_context_items = preparation.omitted_context_items
                overflow_retried = False
                while True:
                    try:
                        response = self._complete_llm(state.messages, context)
                        break
                    except FakeLLMExhaustedError as exc:
                        return self._runtime_failure_result(state, status="llm_exhausted", stop_reason="llm_exhausted", error=str(exc))
                    except LLMContextOverflowError as exc:
                        if self.event_sink is not None:
                            self.event_sink.assistant_message_interrupted(error=_safe_error(exc), turn_id=context.turn_id, attempt_id=context.attempt_id)
                        if exc.output_started or overflow_retried or self.context_recovery is None or self._cancel_requested():
                            if overflow_retried and self.context_recovery is not None:
                                self.context_recovery.retry_exhausted(session_id=context.session_id, turn_id=context.turn_id, attempt_id=context.attempt_id, step=state.step, error=exc)
                            return self._runtime_failure_result(state, status="llm_error", stop_reason="llm_error", error=_safe_error(exc))
                        try:
                            recovered = self.context_recovery.recover_from_provider_overflow(
                                session_id=context.session_id,
                                turn_id=context.turn_id,
                                attempt_id=context.attempt_id,
                                step=state.step,
                                task=state.task,
                                evidence=_checkpoint_evidence(state),
                                original_messages=list(state.messages),
                                original_base_message_count=state.base_message_count,
                                error=exc,
                            )
                        except Exception as recovery_error:
                            return self._runtime_failure_result(state, status="llm_error", stop_reason="llm_error", error=_safe_error(recovery_error))
                        state.messages = recovered.messages
                        state.base_message_count = recovered.base_message_count
                        state.base_context_items = recovered.selected_context_items
                        state.omitted_context_items = recovered.omitted_context_items
                        overflow_retried = True
                    except Exception as exc:
                        if self.event_sink is not None:
                            self.event_sink.assistant_message_interrupted(error=_safe_error(exc), turn_id=context.turn_id, attempt_id=context.attempt_id)
                        return self._runtime_failure_result(state, status="llm_error", stop_reason="llm_error", error=_safe_error(exc))
                if self.event_sink is not None:
                    self.event_sink.assistant_message_completed(
                        content=response.content,
                        reasoning_replay=response.reasoning_replay,
                        turn_id=context.turn_id,
                        attempt_id=context.attempt_id,
                    )
                # 模型调用可能耗时，返回后必须再次检查，取消时不再处理这次响应。
                if self._cancel_requested():
                    return self._cancelled_result(state)
                self.trace_logger.record_llm_call(
                    model=response.model,
                    message_count=len(state.messages),
                    response_text=response.content,
                    usage=response.usage,
                    metadata={
                        "native_tool_count": len(response.tool_calls),
                        "provider_tool_call_ids": [call.provider_tool_call_id for call in response.tool_calls],
                        "tool_names": [call.name for call in response.tool_calls],
                    },
                )
                if response.tool_calls:
                    result = self._handle_tool_calls(state=state, context=context, response=response)
                    if result is not None:
                        return result
                    continue
                result = self._handle_natural_reply(
                    state,
                    response_content=response.content,
                    reasoning_replay=response.reasoning_replay,
                )
                if result is not None:
                    return result
                continue
        except KeyboardInterrupt:
            raise
        return self._runtime_failure_result(
            state,
            status="max_steps_exceeded",
            stop_reason="max_steps",
        )

    def run(self, task: str, repo: str | Path) -> AgentRunResult:
        """Run one Native tool-calling turn for the CLI entry point."""

        repository = Path(repo).resolve()
        return self.run_turn(
            TurnExecutionContext(
                session_id=None,
                turn_id=None,
                attempt_id=None,
                task=task,
                repo=repository,
                messages=build_initial_messages(
                    task,
                    repository,
                    extra_tool_specs=self.prompt_extra_tool_specs,
                    tool_specs=self.visible_tool_specs,
                ),
            )
        )
