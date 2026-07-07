# MCP Tool Integration Acceptance

1. `codepilot mcp-tools --mcp-config examples/mcp/fake_filesystem_mcp.json`
2. `codepilot mcp-call mcp.filesystem.read_file '{"path":"README.md"}' --mcp-config examples/mcp/fake_filesystem_mcp.json`
3. `codepilot mcp-call mcp.filesystem.write_file '{"path":"demo.txt","content":"hello"}' --mcp-config examples/mcp/fake_filesystem_mcp.json`
4. `codepilot mcp-call mcp.filesystem.write_file '{"path":"demo.txt","content":"hello"}' --mcp-config examples/mcp/fake_filesystem_mcp.json --approve`
5. `codepilot mcp-call mcp.filesystem.publish_release '{"version":"1.0.0"}' --mcp-config examples/mcp/fake_filesystem_mcp.json --approve`
6. `codepilot agent-run "Use MCP to read README and summarize it" --repo . --fake-actions examples/mcp/fake_actions_mcp_read_file.jsonl --mcp-config examples/mcp/fake_filesystem_mcp.json --approve`
7. trace contains `mcp=true`, `server_name`, `mcp_tool_name`, `codepilot_tool_name`, `descriptor_hash`.
8. trace does not contain cleartext `token`, `secret`, `password`, `api_key`, or `env` values.
