from __future__ import annotations


class ToolRouteError(RuntimeError):
    """工具路由阶段可被上层区分处理的错误基类。"""


class ToolPreExecutionError(FileNotFoundError, ToolRouteError):
    """恢复令牌构建失败，确认工具尚未产生副作用。"""

    def __init__(self, tool_call_id: str | None, error: Exception) -> None:
        self.tool_call_id = tool_call_id
        super().__init__(str(error))


class ToolExecutionUncertainError(ToolRouteError):
    """工具已进入执行阶段但结果未知，当前 Attempt 必须停止。"""

    def __init__(self, tool_call_id: str | None, tool_name: str, error: Exception) -> None:
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        super().__init__(str(error))
