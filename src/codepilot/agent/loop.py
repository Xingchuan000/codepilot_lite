from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codepilot.agent.actions import (
    AgentFinishAction,
    AgentToolCallAction,
    agent_action_dict_to_trace_preview,
    agent_action_to_trace_input,
    parse_agent_action_with_metadata,
)
from codepilot.agent.observation import format_finish_blocked_observation, format_observation, format_parse_error_observation
from codepilot.agent.prompts import build_initial_messages
from codepilot.agent.state import create_initial_state, mark_finished_from_action, update_state_from_route_result
from codepilot.llm.fake import FakeLLMExhaustedError
from codepilot.llm.types import ChatMessage, CodePilotLLMClient
from codepilot.router import ToolAction, ToolRouter
from codepilot.trace.logger import TraceLogger


@dataclass(frozen=True)
class AgentRunResult:
    """MinimalAgentLoop 对外暴露的最小结果。"""

    success: bool
    status: str
    summary: str
    steps: int
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


class MinimalAgentLoop:
    """按“模型输出一个 JSON action，loop 执行一次”工作的最小闭环。"""

    def __init__(
        self,
        *,
        llm: CodePilotLLMClient,
        router: ToolRouter,
        trace_logger: TraceLogger | None = None,
        max_steps: int = 12,
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

    def _result(
        self,
        *,
        status: str,
        summary: str,
        steps: int,
        changed_files: list[str],
        last_test_status: str | None,
        success: bool,
        error: str | None = None,
        policy_violations: int = 0,
    ) -> AgentRunResult:
        """统一构造返回值。"""

        return AgentRunResult(
            success=success,
            status=status,
            summary=summary,
            steps=steps,
            changed_files=changed_files,
            last_test_status=last_test_status,
            trace_path=str(self.trace_logger.trace_path),
            error=error,
            policy_violations=policy_violations,
        )

    def run(self, task: str, repo: str | Path) -> AgentRunResult:
        """执行最小 LLM loop。"""

        state = create_initial_state(task, repo, max_steps=self.max_steps)
        state.messages = build_initial_messages(task, state.repo)
        self.trace_logger.record_run_start(
            task=task,
            metadata={"source": "minimal_agent_loop", "repo": str(state.repo), "max_steps": self.max_steps},
        )
        try:
            while state.step < state.max_steps and not state.finished:
                state.step += 1
                response = self.llm.complete(state.messages)
                self.trace_logger.record_llm_call(
                    model=response.model,
                    message_count=len(state.messages),
                    response_text=response.content,
                    usage=response.usage,
                )
                try:
                    parsed_action = parse_agent_action_with_metadata(response.content)
                    action = parsed_action.action
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
                if isinstance(action, AgentFinishAction):
                    if action.status == "success" and state.last_test_status != "passed":
                        observation = format_finish_blocked_observation(
                            last_test_status=state.last_test_status,
                            last_test_command=state.last_test_command,
                        )
                        self.trace_logger.record_agent_action(
                            action_type=action.type,
                            tool_name=None,
                            input=agent_action_to_trace_input(action),
                            success=False,
                            error="finish success blocked without passed tests",
                            metadata={
                                "finish_blocked_without_passed_tests": True,
                                "requested_status": action.status,
                                "last_test_status": state.last_test_status,
                                "last_test_command": state.last_test_command,
                            },
                        )
                        state.messages.append(ChatMessage(role="assistant", content=response.content))
                        state.messages.append(ChatMessage(role="user", content=observation))
                        self.trace_logger.record_agent_observation(
                            tool_name=None,
                            observation=observation,
                            metadata={
                                "finish_blocked_without_passed_tests": True,
                                "last_test_status": state.last_test_status,
                                "last_test_command": state.last_test_command,
                            },
                        )
                        continue
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
                if isinstance(action, AgentFinishAction):
                    mark_finished_from_action(state, action)
                    self.trace_logger.record_agent_finish(
                        status=action.status,
                        summary=action.summary,
                        metadata={"tests": action.tests, "changed_files": action.changed_files},
                    )
                    run_end_metadata: dict[str, Any] = {}
                    self.trace_logger.record_run_end(
                        success=action.status == "success",
                        summary=action.summary,
                        metadata=run_end_metadata,
                    )
                    return self._result(
                        status=action.status,
                        summary=action.summary,
                        steps=state.step,
                        changed_files=state.changed_files,
                        last_test_status=state.last_test_status,
                        success=action.status == "success",
                        policy_violations=state.policy_violations,
                    )
                try:
                    injected_args = _inject_repo_if_missing(action.arguments, state.repo)
                    tool_action = ToolAction(
                        tool_name=action.tool_name,
                        arguments=injected_args,
                        reason=action.short_rationale,
                        metadata={
                            "normalization_applied": parsed_action.normalization_metadata.get("normalization_applied", False),
                            "normalized_fields": parsed_action.normalization_metadata.get("normalized_fields", {}),
                        },
                    )
                    route_result = self.router.route(tool_action)
                except Exception as exc:
                    observation = (
                        "Your previous tool_call could not be executed.\n"
                        f"Error: {exc}\n"
                        'Return exactly one JSON object with type "tool_call" or "finish".'
                    )
                    state.messages.append(ChatMessage(role="assistant", content=response.content))
                    state.messages.append(ChatMessage(role="user", content=observation))
                    self.trace_logger.record_agent_observation(tool_name=action.tool_name, observation=observation)
                    continue
                update_state_from_route_result(state, route_result)
                observation = format_observation(route_result)
                state.messages.append(ChatMessage(role="assistant", content=response.content))
                state.messages.append(ChatMessage(role="user", content=observation))
                self.trace_logger.record_agent_observation(
                    tool_name=route_result.tool_name,
                    observation=observation,
                    metadata={"success": route_result.success},
                )
        except FakeLLMExhaustedError as exc:
            self.trace_logger.record_run_end(success=False, summary="llm_exhausted", metadata={"error": str(exc)})
            return self._result(
                status="llm_exhausted",
                summary="llm_exhausted",
                steps=state.step,
                changed_files=state.changed_files,
                last_test_status=state.last_test_status,
                success=False,
                error=str(exc),
                policy_violations=state.policy_violations,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            self.trace_logger.record_run_end(success=False, summary="llm_error", metadata={"error": str(exc)})
            return self._result(
                status="llm_error",
                summary="llm_error",
                steps=state.step,
                changed_files=state.changed_files,
                last_test_status=state.last_test_status,
                success=False,
                error=str(exc),
                policy_violations=state.policy_violations,
            )
        self.trace_logger.record_run_end(success=False, summary="max_steps_exceeded")
        return self._result(
            status="max_steps_exceeded",
            summary="max_steps_exceeded",
            steps=state.step,
            changed_files=state.changed_files,
            last_test_status=state.last_test_status,
            success=False,
            policy_violations=state.policy_violations,
        )
