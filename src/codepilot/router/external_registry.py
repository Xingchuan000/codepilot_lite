from __future__ import annotations

from typing import Any, Protocol

from codepilot.tools.base import ToolResult, ToolSpec


class ExternalToolRegistry(Protocol):
    """当前 MCP 等外部工具边界的正式 registry 契约。"""

    def list_exposed_specs(self) -> list[ToolSpec]: ...

    def find_spec(self, name: str) -> ToolSpec | None: ...

    def has_tool(self, name: str) -> bool: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...
