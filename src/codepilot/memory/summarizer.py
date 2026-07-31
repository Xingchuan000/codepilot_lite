from __future__ import annotations

import json
from typing import Any

from codepilot.llm.types import ChatMessage, CodePilotLLMClient
from codepilot.memory.models import SessionSummaryContent


class StructuredSummaryGenerator:
    """Deterministic fallback that always emits the persisted summary schema."""

    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        max_output_tokens: int = 4_000,
        previous_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = SessionSummaryContent.from_dict(previous_summary) if previous_summary else SessionSummaryContent()
        user_messages = [item for item in messages if item.get("role") == "user"]
        source_ids = tuple(
            dict.fromkeys(
                [
                    *previous.source_message_ids,
                    *(str(item["message_id"]) for item in messages if item.get("message_id")),
                ]
            )
        )
        task_goal = str(user_messages[-1].get("content", ""))[:1000] if user_messages else previous.task_goal
        return SessionSummaryContent(
            task_goal=task_goal,
            user_constraints=previous.user_constraints,
            confirmed_decisions=previous.confirmed_decisions,
            repository_facts=previous.repository_facts,
            files_read=previous.files_read,
            files_modified=previous.files_modified,
            commands_run=previous.commands_run,
            test_results=previous.test_results,
            diff_status=previous.diff_status,
            errors_and_failures=previous.errors_and_failures,
            unresolved_work=("Review the summarized history when continuing this session.",),
            next_actions=previous.next_actions,
            branch=previous.branch,
            commit=previous.commit,
            source_message_ids=source_ids,
        ).to_dict()


class LLMSummaryGenerator:
    def __init__(self, llm: CodePilotLLMClient) -> None:
        self.llm = llm

    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        max_output_tokens: int = 4_000,
        previous_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.llm.complete(
            [
                ChatMessage(
                    "system",
                    "Summarize the supplied session history as one JSON object with exactly these keys: "
                    + ", ".join(SessionSummaryContent().to_dict())
                    + ". Lists must contain concise factual strings. Do not include secrets or markdown.",
                ),
                ChatMessage(
                    "user",
                    json.dumps(
                        {"previous_summary": previous_summary, "messages": messages, "max_output_tokens": max_output_tokens},
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        value = json.loads(response.content)
        return SessionSummaryContent.from_dict(value).to_dict()
