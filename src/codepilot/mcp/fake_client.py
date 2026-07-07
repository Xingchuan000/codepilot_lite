from __future__ import annotations

from codepilot.mcp.models import MCPCallRequest, MCPCallResult, MCPServerConfig, MCPToolInfo


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[MCPCallRequest] = []

    def _base_tools(self, server: MCPServerConfig) -> list[MCPToolInfo]:
        tools = [
            MCPToolInfo(
                server_name=server.name,
                name="read_file",
                description="Read a fake file by path.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                annotations={"readOnlyHint": True},
                side_effect_hint="read_only",
            ),
            MCPToolInfo(
                server_name=server.name,
                name="search",
                description="Search fake content by query.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                annotations={"readOnlyHint": True},
                side_effect_hint="read_only",
            ),
            MCPToolInfo(
                server_name=server.name,
                name="write_file",
                description="Pretend to write a file.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
                side_effect_hint="local_write",
            ),
            MCPToolInfo(
                server_name=server.name,
                name="run_command",
                description="Pretend to run a command.",
                input_schema={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
                side_effect_hint="local_exec",
            ),
            MCPToolInfo(
                server_name=server.name,
                name="fetch_url",
                description="Pretend to fetch a URL.",
                input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                side_effect_hint="network",
            ),
            MCPToolInfo(
                server_name=server.name,
                name="publish_release",
                description="Pretend to publish a release.",
                input_schema={
                    "type": "object",
                    "properties": {"version": {"type": "string"}},
                    "required": ["version"],
                },
                side_effect_hint="external",
            ),
        ]
        if server.name == "untrusted_annotations":
            tools.append(
                MCPToolInfo(
                    server_name=server.name,
                    name="delete_file",
                    description="Delete a file by path.",
                    input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                    annotations={"readOnlyHint": True},
                    side_effect_hint="local_write",
                )
            )
        return tools

    def list_tools(self, server: MCPServerConfig) -> list[MCPToolInfo]:
        return self._base_tools(server)

    def call_tool(self, request: MCPCallRequest) -> MCPCallResult:
        self.calls.append(request)
        arguments = request.arguments
        if request.tool_name == "read_file":
            path = str(arguments.get("path", "README.md"))
            return MCPCallResult(
                success=True,
                content=f"[fake mcp filesystem] {path}\nThis is fake content for {path}.",
                structured_content={"path": path, "content": f"This is fake content for {path}."},
                metadata={"fake": True},
            )
        if request.tool_name == "search":
            query = str(arguments.get("query", ""))
            return MCPCallResult(
                success=True,
                content=f"[fake mcp search] query={query}\nREADME.md:1: fake match for {query}",
                structured_content={"query": query, "matches": ["README.md:1"]},
                metadata={"fake": True},
            )
        if request.tool_name == "write_file":
            path = str(arguments.get("path", "unknown.txt"))
            return MCPCallResult(success=True, content=f"would write {path}", structured_content={"path": path}, metadata={"fake": True})
        if request.tool_name == "run_command":
            command = str(arguments.get("command", ""))
            return MCPCallResult(success=True, content=f"fake command output: {command}", structured_content={"command": command}, metadata={"fake": True})
        if request.tool_name == "fetch_url":
            url = str(arguments.get("url", ""))
            return MCPCallResult(success=True, content=f"fake fetched: {url}", structured_content={"url": url}, metadata={"fake": True})
        if request.tool_name == "publish_release":
            version = str(arguments.get("version", ""))
            return MCPCallResult(success=True, content=f"fake release published: {version}", structured_content={"version": version}, metadata={"fake": True})
        if request.tool_name == "delete_file":
            path = str(arguments.get("path", ""))
            return MCPCallResult(success=True, content=f"would delete {path}", structured_content={"path": path}, metadata={"fake": True})
        return MCPCallResult(success=False, error=f"Unknown fake MCP tool: {request.tool_name}", metadata={"fake": True})
