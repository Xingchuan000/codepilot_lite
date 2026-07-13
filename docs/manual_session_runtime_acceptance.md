# Session Runtime 手动验收文档

本文面向不需要阅读源代码的验收人员。所有命令都在仓库根目录执行；先准备环境：

```bash
export PYTHONPATH=src
export DEMO_ROOT=/tmp/codepilot-session-acceptance
rm -rf "$DEMO_ROOT"
mkdir -p "$DEMO_ROOT/project-a/src" "$DEMO_ROOT/project-b"
printf 'def add(a, b):\n    return a - b\n' > "$DEMO_ROOT/project-a/src/calc.py"
git -C "$DEMO_ROOT/project-a" init
git -C "$DEMO_ROOT/project-a" add .
git -C "$DEMO_ROOT/project-a" -c user.name=acceptance -c user.email=acceptance@example.com commit -m init
```

## 1. 新建 Session

```bash
python -m codepilot.cli tui "$DEMO_ROOT/project-a" --permission-mode manual
```

在 Session Picker 中选择当前项目，或输入 `/new` 创建新 Session。输入：

```text
请检查 src/calc.py，但先不要修改文件。
```

验收：Session 有唯一 ID，项目路径、Provider、模型、权限模式和当前分支可见；未执行导出前，项目目录中不出现 `session.json`、`messages.jsonl`、`runs.jsonl`、`trace.jsonl` 或 `report.json`。

## 2. 两轮连续上下文

第一轮输入：`请读取 src/calc.py，并说明 add 函数的问题。`

第二轮输入：`使用你刚才的第二个建议，但先告诉我将修改哪些文件。`

验收：第二轮能引用第一轮内容；SQLite 中两条 Turn 属于同一 Session，Assistant、Tool Call、Tool Result 分别有结构化记录。

## 3. 退出并重启恢复

输入 `/exit` 退出，再重新执行：

```bash
python -m codepilot.cli tui "$DEMO_ROOT/project-a" --permission-mode manual
```

从 Picker 选择原 Session，输入“继续刚才的工作，并先复述当前未完成事项”。验收：历史来自 SQLite，Transcript 不重复；新消息属于新的 Turn/Attempt。

## 4. 跨项目切换

在 TUI 中输入 `/sessions`，选择 `project-b` 的 Session；也可输入 `/new` 创建新 Session。验收：项目路径、分支和 Session ID 一起切换，旧项目 Transcript 不串入新 Session。

## 5. 路径缺失只读

```bash
mv "$DEMO_ROOT/project-a" "$DEMO_ROOT/project-a-missing"
```

重新打开并选择原 Session。验收：历史仍可查看，项目显示 missing/只读标记，提交消息会被拒绝，不会自动重新绑定路径。完成后恢复目录：

```bash
mv "$DEMO_ROOT/project-a-missing" "$DEMO_ROOT/project-a"
```

## 6. 分支变化

```bash
git -C "$DEMO_ROOT/project-a" checkout -b acceptance-branch
```

回到原 Session 提交消息。验收：先显示旧分支和当前分支确认框；选择继续后才创建 Turn，并写入 `branch_changed` Event；取消时不创建 Turn。

## 7. Session 级权限

在 `manual` 模式下请求一次写操作，权限框应显示 `Approve once`、`Approve for session`、`Deny`。选择 `Approve for session`，再执行同一工作区范围的第二次编辑。验收：第二次命中 SQLite Grant，不重复弹窗；`Approve once` 不创建 Grant；Policy deny 仍优先于 Grant。

## 8. pending approval 恢复

在权限框出现时直接关闭 TUI，再重新打开原 Session。验收：显示 pending approval，可继续批准或拒绝；未得到响应前不能执行工具。

## 9. tool execution uncertain 恢复

使用测试代码或故障注入，让工具在 `execution_started` 后、Tool Result 写入前终止。重新打开 Session 后检查 Recovery Modal：应显示 Tool、参数、`execution_started` 时间、对账结果及 `inspect / mark completed / retry / abort` 操作。只读工具可自动重试；写 Shell 或未知外部工具必须人工确认，不能静默重复执行。

## 10. interrupted Assistant

使用流式 Fake Client 或在 Assistant 输出期间终止进程。重新打开 Session，验收：已显示内容仍在 Transcript 中，并标记 `interrupted`；恢复会创建新 Attempt，模型收到中断内容和重新生成完整回答的 System Event，不从最后字符盲目续写。

## 11. 自动和手动 Compact

在 Session 中产生足够长的多轮历史后输入：

```text
/compact
```

也可以使用显式模型窗口触发：

```bash
python - <<'PY'
from pathlib import Path
from codepilot.session import ModelContextProfile, SessionDatabase, CompactionService, resolve_session_paths

database = SessionDatabase(resolve_session_paths().database_path)
database.initialize()
CompactionService(database).compact(
    "<session-id>",
    ModelContextProfile("codepilot", "acceptance", max_input_tokens=1000, supports_reasoning_replay=False),
)
PY
```

验收：生成 `context_summaries` 和 `context_compacted` Event，原始消息仍可查看，ContextAssembler 使用摘要而不是删除原文。

## 12. Compact failure

让摘要函数返回空字符串或缺少必需字段。验收：写入 `context_compaction_failed` Event；不删除旧消息，不调用主模型；修复摘要函数后 `/compact` 可以重试。

## 13. archive/unarchive

在没有运行 Turn 时输入 `/archive`，再输入 `/unarchive <session-id>`。验收：归档 Session 默认不出现在 active Picker；取消归档后重新出现。运行中执行 `/archive` 或切换 Session 应被拒绝。

## 14. 手动导出

只有明确需要导出时执行：

```bash
python - <<'PY'
from pathlib import Path
from codepilot.session import SessionDatabase, SessionExporter, resolve_session_paths

database = SessionDatabase(resolve_session_paths().database_path)
database.initialize()
output = SessionExporter(database).export("<session-id>", Path("/tmp/codepilot-session-exports"))
print(output)
PY
```

验收导出目录包含固定 v2 文件：

```text
manifest.json
session.json
turns.jsonl
messages.jsonl
events.jsonl
trace.jsonl
report.json
artifacts/
```

检查 `manifest.json`、Artifact SHA-256 和文件顺序。删除导出目录后，原 Session 仍可继续运行；Runtime 不从导出目录读取状态。

## 15. 正常运行无导出文件

```bash
rm -rf /tmp/codepilot-session-exports
python -m codepilot.cli tui "$DEMO_ROOT/project-a" --permission-mode manual
```

验收：正常运行只更新 SQLite 和内部 Artifact；以下文件均不存在：`session.json`、`messages.jsonl`、`runs.jsonl`、`trace.jsonl`、`report.json`。只有显式执行 `/export-session` 或调用 `SessionExporter.export()` 时，才允许在导出目录生成这些导出文件。
