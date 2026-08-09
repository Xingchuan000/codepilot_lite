from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from codepilot.tools.base import ToolResult, ToolSpec

RuntimeToolHandler = Callable[[dict[str, Any]], ToolResult]


class RuntimeToolRegistry:
    """只存在于当前 runtime 的动态工具集合。"""

    def __init__(
        self,
        specs: Iterable[ToolSpec] = (),
        handlers: dict[str, RuntimeToolHandler] | None = None,
    ) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._handlers = dict(handlers or {})
        missing = set(self._specs) - set(self._handlers)
        if missing:
            raise ValueError(f"runtime tools missing handlers: {sorted(missing)}")

    def list_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def find_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._specs

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown runtime tool: {name}")
        return handler(dict(arguments))
