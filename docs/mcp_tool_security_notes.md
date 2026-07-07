# MCP Tool Security Notes

1. MCP annotations are hints, not policy. Untrusted `readOnlyHint` cannot grant allow by itself.
2. Discovery, registered specs, and `exposed_to_agent` are separate stages.
3. Denylist wins over allowlist.
4. The agent prompt only receives exposed specs.
5. `descriptor_hash` is used to fail closed when tool descriptors drift.
6. Secrets and environment values must not enter trace, report, or artifact output.
