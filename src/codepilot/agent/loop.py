from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from codepilot.agent.actions import (
    AgentActionParseError,
    AgentFinishAction,
    AgentToolCallAction,
    ParsedAgentAction,
    agent_action_dict_to_trace_preview,
    agent_action_to_trace_input,
    parse_agent_turn,
)
from codepilot.agent.evidence import AssistantStopReason, CompletionKind, EvidenceDecision
from codepilot.agent.observation import format_finish_blocked_observation, format_observation, format_parse_error_observation
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
from codepilot.llm.fake import FakeLLMExhaustedError
from codepilot.llm.types import ChatMessage, CodePilotLLMClient, LLMResponse, LLMStreamEvent, RichChatMessage
from codepilot.router import ToolAction, ToolRouter
from codepilot.router.errors import ToolExecutionUncertainError, ToolPreExecutionError
from codepilot.tools.base import ToolSpec
from codepilot.tools.registry import list_tool_specs
from codepilot.trace.logger import TraceLogger
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


class AgentEventSink(Protocol):
    """Loop 向 Session 持久化层发布的最小语义事件接口。"""

    def assistant_message_started(self, **kwargs: Any) -> None: ...
    def assistant_text_delta(self, **kwargs: Any) -> None: ...
    def assistant_message_completed(self, **kwargs: Any) -> None: ...
    def tool_call_created(self, **kwargs: Any) -> None: ...
    def tool_result_created(self, **kwargs: Any) -> None: ...
    def loop_observation_created(self, **kwargs: Any) -> None: ...
    def agent_finished(self, **kwargs: Any) -> None: ...

    def assistant_message_interrupted(self, **kwargs: Any) -> None: ...


def _inject_repo_if_missing(arguments: dict[str, Any], repo: Path) -> dict[str, Any]:
    """确保模型不能借 repo 参数切换到当前仓库之外。"""

    injected = dict(arguments)
    if "repo" not in injected:
        injected["repo"] = str(repo)
        return injected
    repo_value = injected["repo"]
    if Path(repo_value).expanduser().resolve() != repo.resolve():
        raise ValueError("repo argument must match the current repository")
    injected["repo"] = str(repo)
    return injected


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


def _parsed_action_metadata(parsed_action: ParsedAgentAction) -> dict[str, Any]:
    """集中生成成功解析动作的 Trace 元数据，确保工具和 finish 使用同一契约。"""

    metadata = parsed_action.normalization_metadata
    return {
        "parse_success": True,
        "normalization_applied": metadata.get("normalization_applied", False),
        "normalized_fields": metadata.get("normalized_fields", {}),
        "non_standard_fields": metadata.get("non_standard_fields", []),
        "normalization_conflicts": metadata.get("conflicts", []),
        "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
        "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
    }


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
    """按“模型输出一个 JSON action，loop 执行一次”工作的最小闭环。"""

    def __init__(
        self,
        *,
        llm: CodePilotLLMClient,
        router: ToolRouter,
        trace_logger: TraceRecorder | None = None,
        max_steps: int = 12,
        prompt_extra_tool_specs: list[ToolSpec] | None = None,
        cancellation_token: Any | None = None,
        event_sink: AgentEventSink | None = None,
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
        self.cancellation_token = cancellation_token
        self.event_sink = event_sink
        self.tool_specs_by_name = {}
        for spec in list_tool_specs():
            self.tool_specs_by_name[spec.name] = spec
        for spec in self.prompt_extra_tool_specs:
            existing = self.tool_specs_by_name.get(spec.name)
            if existing is not None and existing != spec:
                raise ValueError(f"Duplicate tool spec: {spec.name}")
            self.tool_specs_by_name[spec.name] = spec

    def _cancel_requested(self) -> bool:
        return bool(self.cancellation_token and self.cancellation_token.is_cancelled())

    def _complete_llm(self, messages: list[ChatMessage | RichChatMessage], context: TurnExecutionContext) -> LLMResponse:
        """优先消费可选流式接口；旧客户端仍走 complete。"""

        stream = getattr(self.llm, "stream", None)
        if stream is None:
            if self.event_sink is not None:
                self.event_sink.assistant_message_started(turn_id=context.turn_id, attempt_id=context.attempt_id, streaming=False)
            return self.llm.complete(messages)  # type: ignore[arg-type]
        content: list[str] = []
        usage: dict[str, Any] = {}
        if self.event_sink is not None:
            self.event_sink.assistant_message_started(turn_id=context.turn_id, attempt_id=context.attempt_id, streaming=True)
        for event in stream(messages):
            if event.type == "text_delta":
                content.append(event.content)
                if self.event_sink is not None:
                    self.event_sink.assistant_text_delta(content=event.content, type="text", provider_format=event.provider_format, replayable=event.replayable)
            elif event.type == "reasoning_delta":
                if self.event_sink is not None:
                    self.event_sink.assistant_text_delta(content=event.content, type="reasoning", provider_format=event.provider_format, replayable=event.replayable)
            elif event.type == "usage":
                usage = event.usage
            elif event.type == "error":
                raise RuntimeError(event.content or "streaming LLM error")
        return LLMResponse(content="".join(content), usage=usage)

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

        # messages 保留模型原始正文，最终 summary 仍使用 parse_agent_turn 规范化后的文本。
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
        parsed_action: ParsedAgentAction,
        resolution: FinishResolution,
        delivery_kind: str,
    ) -> AgentRunResult:
        """统一完成结构化 finish 的动作、状态、Trace 和结果收尾。"""

        self.trace_logger.record_agent_action(
            action_type=action.type,
            tool_name=None,
            input=agent_action_to_trace_input(action),
            success=True,
            metadata={
                **_parsed_action_metadata(parsed_action),
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

    def _handle_evidence_block(
        self,
        *,
        state: AgentState,
        action: AgentFinishAction,
        parsed_action: ParsedAgentAction,
        response_content: str,
        delivery_kind: str,
    ) -> None:
        """记录 Evidence Gate 拒绝并把可执行的下一步反馈给模型。"""

        if delivery_kind == "code_change":
            state.delivery_kind = "code_change"
        self.trace_logger.record_agent_action(
            action_type=action.type,
            tool_name=None,
            input=agent_action_to_trace_input(action),
            success=False,
            error="finish success blocked by evidence gate",
            metadata={
                **_parsed_action_metadata(parsed_action),
                "finish_blocked_by_evidence": True,
                "requested_status": action.status,
                "delivery_kind": delivery_kind,
                **evidence_snapshot(state).to_payload(),
                "last_test_status": state.last_test_status,
            },
        )
        state.messages.append(ChatMessage(role="assistant", content=response_content))
        observation = format_finish_blocked_observation(
            missing_evidence=list(state.missing_evidence),
            last_test_status=state.last_test_status,
            last_test_command=state.last_test_command,
            diff_checked=state.diff_checked,
            written_files=list(state.written_files),
        )
        state.messages.append(ChatMessage(role="user", content=observation))
        if self.event_sink is not None:
            self.event_sink.loop_observation_created(
                content=observation,
                category="evidence_blocked",
                turn_id=self._context_turn_id,
                attempt_id=self._context_attempt_id,
            )
        self.trace_logger.record_agent_observation(
            tool_name=None,
            observation=observation,
            metadata={
                "finish_blocked_by_evidence": True,
                "delivery_kind": delivery_kind,
                **evidence_snapshot(state).to_payload(),
                "last_test_status": state.last_test_status,
            },
        )

    def _record_tool_error(
        self,
        *,
        state: AgentState,
        response_content: str,
        tool_name: str,
        error: Exception,
    ) -> None:
        """把预期的工具准备或执行异常反馈给模型，不吞掉其他阶段的编程错误。"""

        observation = (
            "Your previous tool_call could not be executed.\n"
            f"Error: {error}\n"
            'Use natural text for normal replies, or return one JSON object for tool_call / finish.'
        )
        state.messages.append(ChatMessage(role="assistant", content=response_content))
        state.messages.append(ChatMessage(role="user", content=observation))
        if self.event_sink is not None:
            self.event_sink.loop_observation_created(
                content=observation,
                category="pre_execution_error",
                turn_id=self._context_turn_id,
                attempt_id=self._context_attempt_id,
            )
        self.trace_logger.record_agent_observation(tool_name=tool_name, observation=observation)

    def run_turn(self, context: TurnExecutionContext) -> AgentRunResult:
        """执行一个 Turn；不会重新生成或查询历史消息。"""

        self._context_turn_id = context.turn_id
        self._context_attempt_id = context.attempt_id
        state = create_initial_state(context.task, context.repo, max_steps=self.max_steps, messages=context.messages)
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
                try:
                    response = self._complete_llm(state.messages, context)
                except FakeLLMExhaustedError as exc:
                    return self._runtime_failure_result(
                        state,
                        status="llm_exhausted",
                        stop_reason="llm_exhausted",
                        error=str(exc),
                    )
                except Exception as exc:
                    if self.event_sink is not None:
                        self.event_sink.assistant_message_interrupted(error=str(exc), turn_id=context.turn_id, attempt_id=context.attempt_id)
                    return self._runtime_failure_result(
                        state,
                        status="llm_error",
                        stop_reason="llm_error",
                        error=str(exc),
                    )
                if self.event_sink is not None:
                    self.event_sink.assistant_message_completed(content=response.content, turn_id=context.turn_id, attempt_id=context.attempt_id)
                # 模型调用可能耗时，返回后必须再次检查，取消时不再处理这次响应。
                if self._cancel_requested():
                    return self._cancelled_result(state)
                self.trace_logger.record_llm_call(
                    model=response.model,
                    message_count=len(state.messages),
                    response_text=response.content,
                    usage=response.usage,
                )
                try:
                    turn = parse_agent_turn(response.content)
                except AgentActionParseError as exc:
                    normalization_metadata = exc.normalization_metadata
                    self.trace_logger.record_agent_action(
                        action_type=None,
                        input={},
                        success=False,
                        error=str(exc),
                        metadata={
                            "parse_success": False,
                            "normalization_applied": normalization_metadata.get("normalization_applied", False),
                            "normalized_fields": normalization_metadata.get("normalized_fields", {}),
                            "non_standard_fields": normalization_metadata.get("non_standard_fields", []),
                            "raw_action_preview": agent_action_dict_to_trace_preview(exc.raw_action or {}),
                            "normalized_action_preview": agent_action_dict_to_trace_preview(
                                exc.normalized_action or {}
                            ),
                        },
                    )
                    state.messages.append(ChatMessage(role="assistant", content=response.content))
                    observation = format_parse_error_observation(exc)
                    state.messages.append(ChatMessage(role="user", content=observation))
                    if self.event_sink is not None:
                        self.event_sink.loop_observation_created(
                            content=observation,
                            category="parse_error",
                            turn_id=context.turn_id,
                            attempt_id=context.attempt_id,
                        )
                    self.trace_logger.record_agent_observation(tool_name=None, observation=observation)
                    continue
                if turn.kind == "natural_reply":
                    return self._natural_reply_result(
                        state,
                        response_content=response.content,
                        text=turn.text,
                    )
                action = turn.action
                assert action is not None
                parsed_action = turn.parsed_action
                assert parsed_action is not None
                if isinstance(action, AgentFinishAction):
                    register_finish_claim(state, action)
                    delivery_kind = _infer_finish_delivery_kind(state, action)
                    if delivery_kind == "code_change":
                        state.task_requires_code_delivery = True
                    decision = refresh_evidence_state(state)
                    resolution = _resolve_finish(action, delivery_kind=delivery_kind, evidence=decision)
                    if resolution.blocked_by_evidence:
                        self._handle_evidence_block(
                            state=state,
                            action=action,
                            parsed_action=parsed_action,
                            response_content=response.content,
                            delivery_kind=delivery_kind,
                        )
                        continue
                    return self._finish_from_action(
                        state=state,
                        action=action,
                        parsed_action=parsed_action,
                        resolution=resolution,
                        delivery_kind=delivery_kind,
                    )
                self.trace_logger.record_agent_action(
                    action_type=action.type,
                    tool_name=action.tool_name if isinstance(action, AgentToolCallAction) else None,
                    input=agent_action_to_trace_input(action),
                    success=True,
                    metadata=_parsed_action_metadata(parsed_action),
                )
                try:
                    injected_args = _inject_repo_if_missing(action.arguments, state.repo)
                    register_tool_attempt(
                        state,
                        tool_name=action.tool_name,
                        side_effect=self._tool_side_effect(action.tool_name),
                        arguments=injected_args,
                    )
                except Exception as exc:
                    self._record_tool_error(
                        state=state,
                        response_content=response.content,
                        tool_name=action.tool_name,
                        error=exc,
                    )
                    continue
                refresh_evidence_state(state)
                tool_action = ToolAction(
                    tool_name=action.tool_name,
                    arguments=injected_args,
                    reason=action.short_rationale,
                    metadata={
                        "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                        "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                    },
                )
                if self.event_sink is not None:
                    self.event_sink.tool_call_created(
                        tool_name=action.tool_name,
                        arguments=injected_args,
                        turn_id=context.turn_id,
                        attempt_id=context.attempt_id,
                    )
                # Router 可能等待权限审批，进入前检查可避免取消后继续阻塞。
                if self._cancel_requested():
                    return self._cancelled_result(state)
                try:
                    route_result = self.router.route(tool_action)
                except ToolPreExecutionError as exc:
                    # Router 可能因权限等待被取消而抛错；此时取消状态优先，不生成误导性的工具错误。
                    if self._cancel_requested():
                        return self._cancelled_result(state)
                    self._record_tool_error(
                        state=state,
                        response_content=response.content,
                        tool_name=action.tool_name,
                        error=exc,
                    )
                    continue
                except ToolExecutionUncertainError:
                    # execution_started 之后副作用未知，绝不能把异常伪装成普通
                    # observation；Runtime 会把当前 Attempt 标为 recovery_required。
                    raise
                observation = format_observation(route_result)
                if self.event_sink is not None:
                    self.event_sink.tool_result_created(
                        tool_name=route_result.tool_name,
                        success=route_result.success,
                        content=route_result.result.output or route_result.result.error or "",
                        turn_id=context.turn_id,
                        attempt_id=context.attempt_id,
                        tool_call_id=route_result.metadata.get("tool_call_id"),
                        observation=observation,
                    )
                update_state_from_route_result(state, route_result)
                refresh_evidence_state(state)
                # 权限等待或工具执行结束后再次检查，取消结果优先于工具 observation。
                if self._cancel_requested():
                    return self._cancelled_result(state)
                state.messages.append(ChatMessage(role="assistant", content=response.content))
                state.messages.append(ChatMessage(role="user", content=observation))
                self.trace_logger.record_agent_observation(
                    tool_name=route_result.tool_name,
                    observation=observation,
                    metadata={"success": route_result.success},
                )
        except KeyboardInterrupt:
            raise
        return self._runtime_failure_result(
            state,
            status="max_steps_exceeded",
            stop_reason="max_steps",
        )

    def run(self, task: str, repo: str | Path) -> AgentRunResult:
        """兼容旧 CLI：单次运行仍使用旧的首轮 Prompt。"""

        repository = Path(repo).resolve()
        return self.run_turn(
            TurnExecutionContext(
                session_id=None,
                turn_id=None,
                attempt_id=None,
                task=task,
                repo=repository,
                messages=build_initial_messages(task, repository, extra_tool_specs=self.prompt_extra_tool_specs),
            )
        )
