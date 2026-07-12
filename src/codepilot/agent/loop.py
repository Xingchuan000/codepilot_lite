from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codepilot.agent.actions import (
    AgentFinishAction,
    AgentToolCallAction,
    agent_action_dict_to_trace_preview,
    agent_action_to_trace_input,
    parse_agent_turn,
)
from codepilot.agent.evidence import AssistantStopReason, CompletionKind
from codepilot.agent.observation import format_finish_blocked_observation, format_observation, format_parse_error_observation
from codepilot.agent.prompts import build_initial_messages
from codepilot.agent.state import (
    AgentState,
    create_initial_state,
    mark_finished_from_action,
    refresh_evidence_state,
    register_finish_claim,
    register_tool_attempt,
    update_state_from_route_result,
)
from codepilot.llm.fake import FakeLLMExhaustedError
from codepilot.llm.types import ChatMessage, CodePilotLLMClient
from codepilot.router import ToolAction, ToolRouter
from codepilot.tools.base import ToolSpec
from codepilot.tools.registry import list_tool_specs
from codepilot.trace.logger import TraceLogger


@dataclass(frozen=True)
class AgentRunResult:
    """MinimalAgentLoop 对外暴露的最小结果。"""

    success: bool
    status: str
    summary: str
    steps: int
    completion_kind: CompletionKind | None = None
    assistant_stop_reason: AssistantStopReason | None = None
    delivery_kind: str | None = None
    task_intent: str = "general"
    requires_evidence: bool = False
    evidence_reasons: list[str] = field(default_factory=list)
    write_attempted: bool = False
    write_executed: bool = False
    written_files: list[str] = field(default_factory=list)
    observed_changed_files: list[str] = field(default_factory=list)
    claimed_changed_files: list[str] = field(default_factory=list)
    tests_required: bool = False
    diff_required: bool = False
    diff_checked: bool = False
    missing_evidence: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    last_test_status: str | None = None
    trace_path: str | None = None
    error: str | None = None
    policy_violations: int = 0


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


class MinimalAgentLoop:
    """按“模型输出一个 JSON action，loop 执行一次”工作的最小闭环。"""

    def __init__(
        self,
        *,
        llm: CodePilotLLMClient,
        router: ToolRouter,
        trace_logger: TraceLogger | None = None,
        max_steps: int = 12,
        prompt_extra_tool_specs: list[ToolSpec] | None = None,
        cancellation_token: Any | None = None,
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

    def _result(
        self,
        *,
        state: AgentState,
        status: str,
        summary: str,
        success: bool,
        error: str | None = None,
        completion_kind: CompletionKind | None = None,
        assistant_stop_reason: AssistantStopReason | None = None,
    ) -> AgentRunResult:
        """统一构造返回值。"""

        return AgentRunResult(
            success=success,
            status=status,
            summary=summary,
            steps=state.step,
            completion_kind=completion_kind or state.completion_kind,
            assistant_stop_reason=assistant_stop_reason or state.assistant_stop_reason,
            delivery_kind=state.delivery_kind,
            task_intent=state.task_intent,
            requires_evidence=state.requires_evidence,
            evidence_reasons=list(state.evidence_reasons),
            write_attempted=state.write_attempted,
            write_executed=state.write_executed,
            written_files=list(state.written_files),
            observed_changed_files=list(state.observed_changed_files),
            claimed_changed_files=list(state.claimed_changed_files),
            tests_required=state.tests_required,
            diff_required=state.diff_required,
            diff_checked=state.diff_checked,
            missing_evidence=list(state.missing_evidence),
            changed_files=list(state.changed_files),
            last_test_status=state.last_test_status,
            trace_path=str(self.trace_logger.trace_path),
            error=error,
            policy_violations=state.policy_violations,
        )

    def _tool_side_effect(self, tool_name: str) -> str | None:
        spec = self.tool_specs_by_name.get(tool_name)
        return spec.side_effect.value if spec is not None else None

    def run(self, task: str, repo: str | Path) -> AgentRunResult:
        """执行最小 LLM loop。"""

        state = create_initial_state(task, repo, max_steps=self.max_steps)
        state.messages = build_initial_messages(task, state.repo, extra_tool_specs=self.prompt_extra_tool_specs)
        self.trace_logger.record_run_start(
            task=task,
            metadata={
                "source": "minimal_agent_loop",
                "repo": str(state.repo),
                "max_steps": self.max_steps,
                "task_intent": state.task_intent,
                "task_requires_code_delivery": state.task_requires_code_delivery,
                "requires_evidence": state.requires_evidence,
                "initial_evidence_reasons": list(state.evidence_reasons),
            },
        )
        try:
            while state.step < state.max_steps and not state.finished:
                if self._cancel_requested():
                    self.trace_logger.record_run_cancelled(metadata={"source": "minimal_agent_loop"})
                    state.final_status = "cancelled"
                    state.final_summary = "cancelled"
                    state.assistant_stop_reason = "cancelled"
                    state.completion_kind = "cancelled"
                    return self._result(state=state, status="cancelled", summary="cancelled", success=False, error="cancelled")
                state.step += 1
                if self._cancel_requested():
                    self.trace_logger.record_run_cancelled(metadata={"source": "minimal_agent_loop"})
                    state.final_status = "cancelled"
                    state.final_summary = "cancelled"
                    state.assistant_stop_reason = "cancelled"
                    state.completion_kind = "cancelled"
                    return self._result(state=state, status="cancelled", summary="cancelled", success=False, error="cancelled")
                response = self.llm.complete(state.messages)
                if self._cancel_requested():
                    self.trace_logger.record_run_cancelled(metadata={"source": "minimal_agent_loop"})
                    state.final_status = "cancelled"
                    state.final_summary = "cancelled"
                    state.assistant_stop_reason = "cancelled"
                    state.completion_kind = "cancelled"
                    return self._result(state=state, status="cancelled", summary="cancelled", success=False, error="cancelled")
                self.trace_logger.record_llm_call(
                    model=response.model,
                    message_count=len(state.messages),
                    response_text=response.content,
                    usage=response.usage,
                )
                try:
                    turn = parse_agent_turn(response.content)
                except Exception as exc:
                    normalization_metadata = getattr(exc, "normalization_metadata", {}) or {}
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
                            "raw_action_preview": agent_action_dict_to_trace_preview(getattr(exc, "raw_action", {}) or {}),
                            "normalized_action_preview": agent_action_dict_to_trace_preview(
                                getattr(exc, "normalized_action", {}) or {}
                            ),
                        },
                    )
                    state.messages.append(ChatMessage(role="assistant", content=response.content))
                    observation = format_parse_error_observation(exc)
                    state.messages.append(ChatMessage(role="user", content=observation))
                    self.trace_logger.record_agent_observation(tool_name=None, observation=observation)
                    continue
                if turn.kind == "natural_reply":
                    state.messages.append(ChatMessage(role="assistant", content=response.content))
                    state.assistant_stop_reason = "natural_reply"
                    decision = refresh_evidence_state(state)
                    status = "message_complete" if not decision.requires_evidence else "task_incomplete"
                    completion_kind = "message_complete" if not decision.requires_evidence else "task_incomplete"
                    success = not decision.requires_evidence
                    state.final_status = status
                    state.final_summary = turn.text
                    state.completion_kind = completion_kind
                    self.trace_logger.record_agent_finish(
                        status=status,
                        success=success,
                        summary=turn.text,
                        metadata={
                            "requested_status": None,
                            "effective_status": status,
                            "status_normalized": False,
                            "completion_kind": completion_kind,
                            "assistant_stop_reason": state.assistant_stop_reason,
                            "requires_evidence": state.requires_evidence,
                            "evidence_reasons": list(state.evidence_reasons),
                            "missing_evidence": list(state.missing_evidence),
                            "tests_required": state.tests_required,
                            "diff_required": state.diff_required,
                            "diff_checked": state.diff_checked,
                            "write_attempted": state.write_attempted,
                            "write_executed": state.write_executed,
                            "written_files": list(state.written_files),
                            "observed_changed_files": list(state.observed_changed_files),
                            "claimed_changed_files": list(state.claimed_changed_files),
                            "changed_files": list(state.changed_files),
                        },
                    )
                    self.trace_logger.record_run_end(
                        success=success,
                        summary=turn.text,
                        metadata={
                            "status": status,
                            "completion_kind": completion_kind,
                            "assistant_stop_reason": state.assistant_stop_reason,
                            "requires_evidence": state.requires_evidence,
                            "missing_evidence": list(state.missing_evidence),
                            "tests_required": state.tests_required,
                            "diff_required": state.diff_required,
                            "diff_checked": state.diff_checked,
                        },
                    )
                    return self._result(state=state, status=status, summary=turn.text, success=success, completion_kind=completion_kind)
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
                    if action.status == "failed":
                        self.trace_logger.record_agent_action(
                            action_type=action.type,
                            tool_name=None,
                            input=agent_action_to_trace_input(action),
                            success=True,
                            metadata={
                                "parse_success": True,
                                "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                                "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                                "non_standard_fields": parsed_action.normalization_metadata.get("non_standard_fields", []),
                                "normalization_conflicts": parsed_action.normalization_metadata.get("conflicts", []),
                                "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
                                "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
                                "requested_status": action.status,
                                "effective_status": "failed",
                                "completion_kind": "task_failed",
                                "assistant_stop_reason": "structured_finish",
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "missing_evidence": list(state.missing_evidence),
                            },
                        )
                        mark_finished_from_action(
                            state,
                            action,
                            effective_status="failed",
                            completion_kind="task_failed",
                            delivery_kind=delivery_kind,
                        )
                        self.trace_logger.record_agent_finish(
                            status="failed",
                            success=False,
                            summary=action.summary,
                            metadata={
                                "requested_status": action.status,
                                "effective_status": "failed",
                                "status_normalized": False,
                                "completion_kind": "task_failed",
                                "assistant_stop_reason": state.assistant_stop_reason,
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "evidence_reasons": list(state.evidence_reasons),
                                "missing_evidence": list(state.missing_evidence),
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "diff_checked": state.diff_checked,
                                "write_attempted": state.write_attempted,
                                "write_executed": state.write_executed,
                                "written_files": list(state.written_files),
                                "observed_changed_files": list(state.observed_changed_files),
                                "claimed_changed_files": list(state.claimed_changed_files),
                                "changed_files": list(state.changed_files),
                                "tests": action.tests,
                            },
                        )
                        self.trace_logger.record_run_end(
                            success=False,
                            summary=action.summary,
                            metadata={
                                "status": "failed",
                                "completion_kind": "task_failed",
                                "assistant_stop_reason": state.assistant_stop_reason,
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "missing_evidence": list(state.missing_evidence),
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "diff_checked": state.diff_checked,
                            },
                        )
                        return self._result(state=state, status="failed", summary=action.summary, success=False, completion_kind="task_failed")
                    if action.status == "partial":
                        self.trace_logger.record_agent_action(
                            action_type=action.type,
                            tool_name=None,
                            input=agent_action_to_trace_input(action),
                            success=True,
                            metadata={
                                "parse_success": True,
                                "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                                "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                                "non_standard_fields": parsed_action.normalization_metadata.get("non_standard_fields", []),
                                "normalization_conflicts": parsed_action.normalization_metadata.get("conflicts", []),
                                "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
                                "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
                                "requested_status": action.status,
                                "effective_status": "partial",
                                "completion_kind": "task_partial",
                                "assistant_stop_reason": "structured_finish",
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "missing_evidence": list(state.missing_evidence),
                            },
                        )
                        mark_finished_from_action(
                            state,
                            action,
                            effective_status="partial",
                            completion_kind="task_partial",
                            delivery_kind=delivery_kind,
                        )
                        self.trace_logger.record_agent_finish(
                            status="partial",
                            success=False,
                            summary=action.summary,
                            metadata={
                                "requested_status": action.status,
                                "effective_status": "partial",
                                "status_normalized": False,
                                "completion_kind": "task_partial",
                                "assistant_stop_reason": state.assistant_stop_reason,
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "evidence_reasons": list(state.evidence_reasons),
                                "missing_evidence": list(state.missing_evidence),
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "diff_checked": state.diff_checked,
                                "write_attempted": state.write_attempted,
                                "write_executed": state.write_executed,
                                "written_files": list(state.written_files),
                                "observed_changed_files": list(state.observed_changed_files),
                                "claimed_changed_files": list(state.claimed_changed_files),
                                "changed_files": list(state.changed_files),
                                "tests": action.tests,
                            },
                        )
                        self.trace_logger.record_run_end(
                            success=False,
                            summary=action.summary,
                            metadata={
                                "status": "partial",
                                "completion_kind": "task_partial",
                                "assistant_stop_reason": state.assistant_stop_reason,
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "missing_evidence": list(state.missing_evidence),
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "diff_checked": state.diff_checked,
                            },
                        )
                        return self._result(state=state, status="partial", summary=action.summary, success=False, completion_kind="task_partial")
                    if action.status == "success" and not decision.requires_evidence:
                        self.trace_logger.record_agent_action(
                            action_type=action.type,
                            tool_name=None,
                            input=agent_action_to_trace_input(action),
                            success=True,
                            metadata={
                                "parse_success": True,
                                "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                                "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                                "non_standard_fields": parsed_action.normalization_metadata.get("non_standard_fields", []),
                                "normalization_conflicts": parsed_action.normalization_metadata.get("conflicts", []),
                                "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
                                "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
                                "requested_status": action.status,
                                "effective_status": "message_complete",
                                "status_normalized": True,
                                "completion_kind": "message_complete",
                                "assistant_stop_reason": "structured_finish",
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "missing_evidence": list(state.missing_evidence),
                            },
                        )
                        mark_finished_from_action(
                            state,
                            action,
                            effective_status="message_complete",
                            completion_kind="message_complete",
                            delivery_kind=delivery_kind,
                        )
                        self.trace_logger.record_agent_finish(
                            status="message_complete",
                            success=True,
                            summary=action.summary,
                            metadata={
                                "requested_status": action.status,
                                "effective_status": "message_complete",
                                "status_normalized": True,
                                "completion_kind": "message_complete",
                                "assistant_stop_reason": state.assistant_stop_reason,
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "evidence_reasons": list(state.evidence_reasons),
                                "missing_evidence": list(state.missing_evidence),
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "diff_checked": state.diff_checked,
                                "write_attempted": state.write_attempted,
                                "write_executed": state.write_executed,
                                "written_files": list(state.written_files),
                                "observed_changed_files": list(state.observed_changed_files),
                                "claimed_changed_files": list(state.claimed_changed_files),
                                "changed_files": list(state.changed_files),
                                "tests": action.tests,
                            },
                        )
                        self.trace_logger.record_run_end(
                            success=True,
                            summary=action.summary,
                            metadata={
                                "status": "message_complete",
                                "completion_kind": "message_complete",
                                "assistant_stop_reason": state.assistant_stop_reason,
                                "delivery_kind": delivery_kind,
                                "requires_evidence": state.requires_evidence,
                                "missing_evidence": list(state.missing_evidence),
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "diff_checked": state.diff_checked,
                            },
                        )
                        return self._result(
                            state=state,
                            status="message_complete",
                            summary=action.summary,
                            success=True,
                            completion_kind="message_complete",
                        )
                    if action.status == "success" and decision.missing:
                        if delivery_kind == "code_change":
                            state.delivery_kind = "code_change"
                        self.trace_logger.record_agent_action(
                            action_type=action.type,
                            tool_name=None,
                            input=agent_action_to_trace_input(action),
                            success=False,
                            error="finish success blocked by evidence gate",
                            metadata={
                                "parse_success": True,
                                "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                                "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                                "non_standard_fields": parsed_action.normalization_metadata.get("non_standard_fields", []),
                                "normalization_conflicts": parsed_action.normalization_metadata.get("conflicts", []),
                                "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
                                "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
                                "finish_blocked_by_evidence": True,
                                "requested_status": action.status,
                                "delivery_kind": delivery_kind,
                                "missing_evidence": list(state.missing_evidence),
                                "requires_evidence": state.requires_evidence,
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "write_attempted": state.write_attempted,
                                "write_executed": state.write_executed,
                                "written_files": list(state.written_files),
                                "observed_changed_files": list(state.observed_changed_files),
                                "claimed_changed_files": list(state.claimed_changed_files),
                                "last_test_status": state.last_test_status,
                                "diff_checked": state.diff_checked,
                            },
                        )
                        state.messages.append(ChatMessage(role="assistant", content=response.content))
                        observation = format_finish_blocked_observation(
                            missing_evidence=list(state.missing_evidence),
                            last_test_status=state.last_test_status,
                            last_test_command=state.last_test_command,
                            diff_checked=state.diff_checked,
                            written_files=list(state.written_files),
                        )
                        state.messages.append(ChatMessage(role="user", content=observation))
                        self.trace_logger.record_agent_observation(
                            tool_name=None,
                            observation=observation,
                            metadata={
                                "finish_blocked_by_evidence": True,
                                "missing_evidence": list(state.missing_evidence),
                                "requires_evidence": state.requires_evidence,
                                "tests_required": state.tests_required,
                                "diff_required": state.diff_required,
                                "delivery_kind": delivery_kind,
                                "write_attempted": state.write_attempted,
                                "write_executed": state.write_executed,
                                "written_files": list(state.written_files),
                                "observed_changed_files": list(state.observed_changed_files),
                                "claimed_changed_files": list(state.claimed_changed_files),
                                "last_test_status": state.last_test_status,
                                "diff_checked": state.diff_checked,
                            },
                        )
                        continue
                    self.trace_logger.record_agent_action(
                        action_type=action.type,
                        tool_name=None,
                        input=agent_action_to_trace_input(action),
                        success=True,
                        metadata={
                            "parse_success": True,
                            "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                            "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                            "non_standard_fields": parsed_action.normalization_metadata.get("non_standard_fields", []),
                            "normalization_conflicts": parsed_action.normalization_metadata.get("conflicts", []),
                            "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
                            "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
                            "requested_status": action.status,
                            "effective_status": "success",
                            "status_normalized": False,
                            "completion_kind": "task_success",
                            "assistant_stop_reason": "structured_finish",
                            "requires_evidence": state.requires_evidence,
                            "missing_evidence": list(state.missing_evidence),
                        },
                    )
                    mark_finished_from_action(
                        state,
                        action,
                        effective_status="success",
                        completion_kind="task_success",
                        delivery_kind=delivery_kind,
                    )
                    self.trace_logger.record_agent_finish(
                        status="success",
                        success=True,
                        summary=action.summary,
                        metadata={
                            "requested_status": action.status,
                            "effective_status": "success",
                            "status_normalized": False,
                            "completion_kind": "task_success",
                            "assistant_stop_reason": state.assistant_stop_reason,
                            "requires_evidence": state.requires_evidence,
                            "evidence_reasons": list(state.evidence_reasons),
                            "missing_evidence": list(state.missing_evidence),
                            "tests_required": state.tests_required,
                            "diff_required": state.diff_required,
                            "diff_checked": state.diff_checked,
                            "write_attempted": state.write_attempted,
                            "write_executed": state.write_executed,
                            "written_files": list(state.written_files),
                            "observed_changed_files": list(state.observed_changed_files),
                            "claimed_changed_files": list(state.claimed_changed_files),
                            "changed_files": list(state.changed_files),
                            "tests": action.tests,
                        },
                    )
                    self.trace_logger.record_run_end(
                        success=True,
                        summary=action.summary,
                        metadata={
                            "status": "success",
                            "completion_kind": "task_success",
                            "assistant_stop_reason": state.assistant_stop_reason,
                            "delivery_kind": delivery_kind,
                            "requires_evidence": state.requires_evidence,
                            "missing_evidence": list(state.missing_evidence),
                            "tests_required": state.tests_required,
                            "diff_required": state.diff_required,
                            "diff_checked": state.diff_checked,
                        },
                    )
                    return self._result(state=state, status="success", summary=action.summary, success=True, completion_kind="task_success")
                self.trace_logger.record_agent_action(
                    action_type=action.type,
                    tool_name=action.tool_name if isinstance(action, AgentToolCallAction) else None,
                    input=agent_action_to_trace_input(action),
                    success=True,
                    metadata={
                        "parse_success": True,
                        "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                        "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                        "non_standard_fields": parsed_action.normalization_metadata.get("non_standard_fields", []),
                        "normalization_conflicts": parsed_action.normalization_metadata.get("conflicts", []),
                        "raw_action_preview": agent_action_dict_to_trace_preview(parsed_action.raw_action),
                        "normalized_action_preview": agent_action_dict_to_trace_preview(parsed_action.normalized_action),
                    },
                )
                try:
                    injected_args = _inject_repo_if_missing(action.arguments, state.repo)
                    register_tool_attempt(
                        state,
                        tool_name=action.tool_name,
                        side_effect=self._tool_side_effect(action.tool_name),
                        arguments=injected_args,
                    )
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
                    if self._cancel_requested():
                        self.trace_logger.record_run_cancelled(metadata={"source": "minimal_agent_loop"})
                        state.final_status = "cancelled"
                        state.final_summary = "cancelled"
                        state.assistant_stop_reason = "cancelled"
                        state.completion_kind = "cancelled"
                        return self._result(state=state, status="cancelled", summary="cancelled", success=False, error="cancelled")
                    route_result = self.router.route(tool_action)
                except Exception as exc:
                    observation = (
                        "Your previous tool_call could not be executed.\n"
                        f"Error: {exc}\n"
                        'Use natural text for normal replies, or return one JSON object for tool_call / finish.'
                    )
                    state.messages.append(ChatMessage(role="assistant", content=response.content))
                    state.messages.append(ChatMessage(role="user", content=observation))
                    self.trace_logger.record_agent_observation(tool_name=action.tool_name, observation=observation)
                    continue
                update_state_from_route_result(state, route_result)
                refresh_evidence_state(state)
                if self._cancel_requested():
                    self.trace_logger.record_run_cancelled(metadata={"source": "minimal_agent_loop"})
                    state.final_status = "cancelled"
                    state.final_summary = "cancelled"
                    state.assistant_stop_reason = "cancelled"
                    state.completion_kind = "cancelled"
                    return self._result(state=state, status="cancelled", summary="cancelled", success=False, error="cancelled")
                observation = format_observation(route_result)
                state.messages.append(ChatMessage(role="assistant", content=response.content))
                state.messages.append(ChatMessage(role="user", content=observation))
                self.trace_logger.record_agent_observation(
                    tool_name=route_result.tool_name,
                    observation=observation,
                    metadata={"success": route_result.success},
                )
        except FakeLLMExhaustedError as exc:
            state.final_status = "llm_exhausted"
            state.final_summary = "llm_exhausted"
            state.assistant_stop_reason = "llm_exhausted"
            state.completion_kind = "runtime_failure"
            self.trace_logger.record_run_end(success=False, summary="llm_exhausted", metadata={"status": "llm_exhausted", "error": str(exc), "completion_kind": "runtime_failure", "assistant_stop_reason": "llm_exhausted"})
            return self._result(state=state, status="llm_exhausted", summary="llm_exhausted", success=False, error=str(exc), completion_kind="runtime_failure")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            state.final_status = "llm_error"
            state.final_summary = "llm_error"
            state.assistant_stop_reason = "llm_error"
            state.completion_kind = "runtime_failure"
            self.trace_logger.record_run_end(success=False, summary="llm_error", metadata={"status": "llm_error", "error": str(exc), "completion_kind": "runtime_failure", "assistant_stop_reason": "llm_error"})
            return self._result(state=state, status="llm_error", summary="llm_error", success=False, error=str(exc), completion_kind="runtime_failure")
        state.final_status = "max_steps_exceeded"
        state.final_summary = "max_steps_exceeded"
        state.assistant_stop_reason = "max_steps"
        state.completion_kind = "runtime_failure"
        self.trace_logger.record_run_end(success=False, summary="max_steps_exceeded", metadata={"status": "max_steps_exceeded", "completion_kind": "runtime_failure", "assistant_stop_reason": "max_steps"})
        return self._result(state=state, status="max_steps_exceeded", summary="max_steps_exceeded", success=False, completion_kind="runtime_failure")
