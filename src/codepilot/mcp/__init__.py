from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codepilot.mcp.registry import MCPToolRegistry as MCPToolRegistry

__all__ = ["MCPToolRegistry", "load_mcp_config"]


def __getattr__(name: str):
    if name == "MCPToolRegistry":
        from codepilot.mcp.registry import MCPToolRegistry

        return MCPToolRegistry
    if name == "load_mcp_config":
        from codepilot.mcp.config import load_mcp_config

        return load_mcp_config
    raise AttributeError(name)
