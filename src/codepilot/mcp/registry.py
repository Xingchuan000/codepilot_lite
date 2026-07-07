from __future__ import annotations

from pathlib import Path
from typing import Any

from codepilot.mcp.adapter import mcp_result_to_tool_result
from codepilot.mcp.client import MCPClientProtocol, StdioMCPClient
from codepilot.mcp.config import load_mcp_config
from codepilot.mcp.exposure import mark_exposure_on_spec, should_expose_mcp_tool
from codepilot.mcp.fake_client import FakeMCPClient
from codepilot.mcp.models import MCPCallRequest, MCPServerConfig, MCPToolBinding, MCPToolInfo
from codepilot.mcp.risk import classify_mcp_tool
from codepilot.mcp.trace import build_codepilot_mcp_tool_name, build_mcp_descriptor_hash
from codepilot.tools.base import ToolResult, ToolSpec
from codepilot.tools.registry import find_tool_spec


class MCPToolRegistry:
    def __init__(
        self,
        servers: dict[str, MCPServerConfig],
        specs: dict[str, ToolSpec],
        exposed_specs: dict[str, ToolSpec],
        bindings: dict[str, MCPToolBinding],
        tool_infos: dict[str, MCPToolInfo],
        clients: dict[str, MCPClientProtocol],
    ) -> None:
        self.servers = servers
        self._specs = specs
        self._exposed_specs = exposed_specs
        self._bindings = bindings
        self._tool_infos = tool_infos
        self._clients = clients

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        client: MCPClientProtocol | None = None,
    ) -> "MCPToolRegistry":
        servers = {server.name: server for server in load_mcp_config(config_path)}
        specs: dict[str, ToolSpec] = {}
        exposed_specs: dict[str, ToolSpec] = {}
        bindings: dict[str, MCPToolBinding] = {}
        tool_infos: dict[str, MCPToolInfo] = {}
        clients: dict[str, MCPClientProtocol] = {}

        for server in servers.values():
            if not server.enabled:
                continue
            if server.trust_level == "remote_untrusted":
                if server.required:
                    raise ValueError(f"MCP server {server.name} is remote_untrusted and required")
                continue

            effective_client: MCPClientProtocol
            if server.transport == "fake":
                effective_client = client or FakeMCPClient()
            else:
                effective_client = StdioMCPClient()
            clients[server.name] = effective_client

            try:
                tools = effective_client.list_tools(server)
            except Exception as exc:
                if server.required:
                    raise ValueError(f"Failed to start MCP server {server.name}: {exc}") from exc
                continue

            for index, raw_tool in enumerate(tools):
                tool = raw_tool.model_copy()
                tool_name = tool.name
                codepilot_tool_name = build_codepilot_mcp_tool_name(server.name, tool_name)
                if find_tool_spec(codepilot_tool_name) is not None:
                    raise ValueError(f"MCP tool name conflicts with core tool: {codepilot_tool_name}")
                if codepilot_tool_name in specs:
                    raise ValueError(f"Duplicate MCP tool name: {codepilot_tool_name}")
                if tool_name in server.tool_denylist:
                    bindings[codepilot_tool_name] = MCPToolBinding(
                        server_name=server.name,
                        mcp_tool_name=tool_name,
                        codepilot_tool_name=codepilot_tool_name,
                        status="disabled",
                        reason="tool_denylisted",
                        exposed_to_agent=False,
                        transport=server.transport,
                        trust_level=server.trust_level,
                    )
                    continue

                descriptor_hash = tool.descriptor_hash or build_mcp_descriptor_hash(tool)
                tool = tool.model_copy(update={"descriptor_hash": descriptor_hash})
                spec = classify_mcp_tool(tool, server=server)
                exposed, reason = should_expose_mcp_tool(server, tool, spec, index=index)
                spec = mark_exposure_on_spec(spec, exposed=exposed, reason=reason)
                specs[codepilot_tool_name] = spec
                tool_infos[codepilot_tool_name] = tool
                if exposed:
                    exposed_specs[codepilot_tool_name] = spec
                bindings[codepilot_tool_name] = MCPToolBinding(
                    server_name=server.name,
                    mcp_tool_name=tool.name,
                    codepilot_tool_name=codepilot_tool_name,
                    status="available",
                    reason=reason,
                    exposed_to_agent=exposed,
                    descriptor_hash=descriptor_hash,
                    risk_source=spec.metadata.get("risk_source", "heuristic"),
                    transport=server.transport,
                    trust_level=server.trust_level,
                    config_hash=spec.metadata.get("config_hash"),
                )

        return cls(servers, specs, exposed_specs, bindings, tool_infos, clients)

    def list_specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def list_exposed_specs(self) -> list[ToolSpec]:
        return [self._exposed_specs[name] for name in sorted(self._exposed_specs)]

    def list_bindings(self) -> list[MCPToolBinding]:
        return [self._bindings[name] for name in sorted(self._bindings)]

    def find_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._specs

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        binding = self._bindings.get(name)
        if binding is None:
            return ToolResult(success=False, error=f"Unknown MCP tool: {name}", metadata={"mcp": True, "executed": False})
        if binding.status != "available":
            return ToolResult(
                success=False,
                error=f"MCP tool is not available: {name}",
                metadata={"mcp": True, "executed": False, "status": binding.status, "reason": binding.reason},
            )
        tool = self._tool_infos[name]
        server = self.servers[binding.server_name]
        client = self._clients[binding.server_name]
        try:
            refreshed = client.list_tools(server)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"MCP client error while refreshing descriptor: {exc}",
                metadata={"mcp": True, "executed": False, "status": "failed"},
            )
        refreshed_tool = next((item for item in refreshed if item.name == binding.mcp_tool_name), None)
        if refreshed_tool is None:
            return ToolResult(success=False, error="descriptor_hash_mismatch", metadata={"mcp": True, "executed": False})
        refreshed_hash = build_mcp_descriptor_hash(refreshed_tool)
        if refreshed_hash != binding.descriptor_hash:
            return ToolResult(success=False, error="descriptor_hash_mismatch", metadata={"mcp": True, "executed": False})
        request = MCPCallRequest(
            server_name=server.name,
            tool_name=binding.mcp_tool_name,
            arguments=arguments,
            timeout_seconds=server.tool_timeout_seconds,
            max_output_chars=server.max_output_chars,
        )
        try:
            result = client.call_tool(request)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"MCP client error: {exc}",
                metadata={"mcp": True, "executed": False, "server_name": server.name},
            )
        return mcp_result_to_tool_result(
            result,
            server=server,
            tool=tool,
            codepilot_tool_name=name,
            max_output_chars=server.max_output_chars,
        )
