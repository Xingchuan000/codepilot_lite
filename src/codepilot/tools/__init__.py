"""CodePilot Lite 工具层导出。

这里只导出基础类型，避免把工具实现和外部调用强绑定。
"""

from codepilot.tools.base import ExternalImpact, Reversibility, ToolIdempotency, ToolRecoveryStrategy, ToolResult, ToolRisk, ToolSideEffect, ToolSpec

__all__ = [
    "ExternalImpact",
    "Reversibility",
    "ToolResult",
    "ToolIdempotency",
    "ToolRecoveryStrategy",
    "ToolRisk",
    "ToolSideEffect",
    "ToolSpec",
]
