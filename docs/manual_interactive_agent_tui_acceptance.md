# Interactive Agent TUI 手动验收

## 1. 准备 demo repo

```bash
mkdir -p /tmp/codepilot-tui-demo/src /tmp/codepilot-tui-demo/tests
```

## 2. fake-actions 验收

```bash
PYTHONPATH=src python -m codepilot.cli tui /tmp/codepilot-tui-demo \
  --fake-actions tests/codepilot/fixtures/tui_agent_actions_success.jsonl \
  --permission-mode manual
```

## 3. 权限弹窗验收

输入：修复 add 函数错误，并运行测试

期望：`replace_range` 出现 `Approve once` / `Deny`

## 4. 结果验收

期望：`status success`、`tests passed`、`report.md`、`trace.jsonl`、`/diff`、`/report`、`/new`、`/exit` 可用

## 5. cancel 验收

运行中输入 `/cancel`，期望 `run_end status=cancelled`

## 6. dashboard 兼容验收

```bash
PYTHONPATH=src python -m codepilot.cli dashboard --runs-dir /tmp/codepilot-tui-demo/runs --static
```

