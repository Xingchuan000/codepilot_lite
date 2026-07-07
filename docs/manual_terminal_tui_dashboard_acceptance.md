# Terminal TUI Dashboard Acceptance

## Commands

```bash
PYTHONPATH=src python -m codepilot.cli dashboard --runs-dir runs --limit 10 --static
PYTHONPATH=src python -m codepilot.cli dashboard --runs-dir runs --run-id mcp-agent-demo-test --static
PYTHONPATH=src python -m codepilot.cli dashboard --runs-dir runs --limit 5 --json
PYTHONPATH=src python -m codepilot.cli dashboard --runs-dir runs --run-id mcp-agent-demo-test --json
```

## Acceptance

1. Index shows run_id, status, tools, policy, tests, artifacts, MCP, and task.
2. Detail shows timeline, policy, test, diff, MCP, artifacts, and warnings.
3. JSON can be parsed by `json.loads`.
4. Output does not contain token, password, api_key, authorization, cookie, or private key values.
5. Policy deny and unapproved ask entries are not shown as successful tool calls.
6. Dashboard does not modify any file mtime under `runs/`.
