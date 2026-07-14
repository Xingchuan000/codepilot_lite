from __future__ import annotations


class ContextBudgetExceeded(RuntimeError):
    """最终模型输入超过 Provider 声明的上下文预算。"""


class ContextBudgetAllocator:
    """按字符统一分配一次模型调用的全局输入预算。"""

    def __init__(self, max_input_tokens: int) -> None:
        self._remaining = max_input_tokens * 4

    def reserve_system(self, content: str) -> str:
        return self._consume(content)

    def consume_summary(self, content: str) -> str:
        return self._consume(content)

    def consume_message(self, content: str) -> str:
        return self._consume(content)

    def remaining_chars(self) -> int:
        return self._remaining

    def _consume(self, content: str) -> str:
        if self._remaining <= 0:
            return ""
        if len(content) <= self._remaining:
            result = content
        else:
            marker = "\n...[context truncated]"
            result = content[: self._remaining] if self._remaining <= len(marker) else content[: self._remaining - len(marker)] + marker
        self._remaining -= len(result)
        return result
