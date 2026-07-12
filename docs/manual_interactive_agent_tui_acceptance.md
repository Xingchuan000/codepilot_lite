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

## 5. list_files 分页验收

```bash
mkdir -p /tmp/codepilot-list-files-pagination
python - <<'PY'
from pathlib import Path

root = Path("/tmp/codepilot-list-files-pagination")
for index in range(450):
    (root / f"file_{index:04d}.txt").write_text(str(index), encoding="utf-8")
PY
PYTHONPATH=src python -m codepilot.cli tui-agent \
  --repo /tmp/codepilot-list-files-pagination
```

在对话里输入：

```text
请完整检查当前目录中的所有文件名，并告诉我文件总数。
```

检查 trace：

- `list_files` 的 `offset` 依次推进为 `0`、`200`、`400`
- 不要通过把 `max_entries` 调到极大值来绕过分页
- 最终回答应说明已经按多页读取完成，而不是停在第一页

## 6. cancel 验收

运行中输入 `/cancel`，期望 `run_end status=cancelled`

## 7. dashboard 兼容验收

```bash
PYTHONPATH=src python -m codepilot.cli dashboard --runs-dir /tmp/codepilot-tui-demo/runs --static
```
