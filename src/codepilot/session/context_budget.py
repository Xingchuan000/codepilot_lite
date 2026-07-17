from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codepilot.llm.types import ChatMessage, RichChatMessage


class ContextBudgetExceeded(RuntimeError):
    """上下文中不可拆分的必需项超过模型输入预算。"""

    def __init__(self, message: str = "context budget exceeded", *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


def estimate_tokens(value: object) -> int:
    """使用规划和最终校验共同使用的保守字符估算器。

    当前项目尚未给每个 Provider 接入 tokenizer，因此所有上下文规划都使用同一套
    保守估算，避免“规划时一种算法、发送前又换另一种算法”的预算漂移。
    """

    return max(1, (len(str(value)) + 3) // 4)


@dataclass(frozen=True)
class ContextItem:
    """上下文中的最小不可拆分单元。

    Tool Call 和 Tool Result 会共享同一个 ``atomic_group``，并被组装为同一个
    ContextItem；因此预算不足时只能整体保留或整体跳过，不能留下半条工具事实。
    """

    key: str
    messages: tuple[ChatMessage | RichChatMessage, ...]
    estimated_tokens: int
    mandatory: bool
    priority: int
    atomic_group: str | None = None


@dataclass(frozen=True)
class ContextPlan:
    """按业务优先级分组后的完整上下文计划。"""

    system_items: tuple[ContextItem, ...]
    summary_items: tuple[ContextItem, ...]
    history_items: tuple[ContextItem, ...]
    current_turn_items: tuple[ContextItem, ...]

    def ordered_items(self) -> tuple[ContextItem, ...]:
        """按最终发送给模型的时间顺序返回规划项。"""

        # history_items 为预算分配按新到旧排列；对外暴露的完整顺序必须恢复为旧到新，
        # 并将当前 Turn 放在历史末尾，避免调用方再次得到倒置的对话上下文。
        return self.system_items + self.summary_items + tuple(reversed(self.history_items)) + self.current_turn_items


class ContextBudgetAllocator:
    """只做整组预算分配，不对消息正文做字符级截断。"""

    def __init__(self, max_input_tokens: int, *, protocol_overhead_tokens: int = 0) -> None:
        self.max_input_tokens = max_input_tokens
        self.protocol_overhead_tokens = protocol_overhead_tokens
        self._limit = max_input_tokens - protocol_overhead_tokens
        if self._limit < 0:
            raise ContextBudgetExceeded("protocol overhead exceeds context budget", reason="protocol_overhead")
        self._used = 0
        self._selected: list[ContextItem] = []

    @property
    def used_tokens(self) -> int:
        return self._used

    @property
    def remaining_tokens(self) -> int:
        return self._limit - self._used

    def require(self, item: ContextItem) -> None:
        """加入必需项；放不下时立即阻断当前 Turn。"""

        if item.estimated_tokens > self.remaining_tokens:
            raise ContextBudgetExceeded(
                f"mandatory context item {item.key!r} does not fit in the input budget",
                reason="mandatory_item_too_large",
            )
        self._selected.append(item)
        self._used += item.estimated_tokens

    def try_add(self, item: ContextItem) -> bool:
        """尝试加入可选项；放不下时整组跳过，不修改已选上下文。"""

        if item.estimated_tokens > self.remaining_tokens:
            return False
        self._selected.append(item)
        self._used += item.estimated_tokens
        return True

    def selected_items(self) -> tuple[ContextItem, ...]:
        return tuple(self._selected)

    def verify(self, messages: list[ChatMessage | RichChatMessage]) -> None:
        """在交给 Provider 前用同一估算器校验最终消息总量。"""

        actual = sum(estimate_tokens(message) for message in messages)
        if actual + self.protocol_overhead_tokens > self.max_input_tokens:
            raise ContextBudgetExceeded("final provider context exceeds input budget", reason="final_context_overflow")
