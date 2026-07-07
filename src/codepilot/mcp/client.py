from __future__ import annotations

from typing import Protocol

from codepilot.mcp.models import MCPCallRequest, MCPCallResult, MCPServerConfig, MCPToolInfo


class MCPClientProtocol(Protocol):
    def list_tools(self, server: MCPServerConfig) -> list[MCPToolInfo]: ...

    def call_tool(self, request: MCPCallRequest) -> MCPCallResult: ...


class StdioMCPClient:
    def __init__(self) -> None:
        self._error = "MCP SDK is not installed; stdio transport is unavailable"

    def list_tools(self, server: MCPServerConfig) -> list[MCPToolInfo]:
        raise RuntimeError(self._error)

    def call_tool(self, request: MCPCallRequest) -> MCPCallResult:
        return MCPCallResult(success=False, error=self._error, metadata={"transport": "stdio", "server_name": request.server_name})
