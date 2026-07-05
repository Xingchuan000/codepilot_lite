# 第十一步手动验收

## 1. clean repo 普通流程

```bash
codepilot issue --issue-file issue.md --repo . --approve --overwrite
```

预期结果：
- 命令成功退出。
- `runs/<run_id>/artifact_manifest.json` 存在。
- `runs/<run_id>/restore_plan.md` 存在。
- `pr_summary.md` 包含 `## Safety` 和 `## Patch Metadata`。

## 2. dirty repo + --dirty-policy fail

```bash
codepilot issue --issue-file issue.md --repo . --dirty-policy fail --overwrite
```

预期结果：
- 命令非 0 退出。
- 输出包含 `Repo safety denied: repository has uncommitted changes.`。
- `issue.json`、`pr_summary.md`、`artifact_manifest.json` 仍然存在。
- 不会启动 agent run。

## 3. dirty repo + --dirty-policy warn

```bash
codepilot issue --issue-file issue.md --repo . --dirty-policy warn --approve --overwrite
```

预期结果：
- 命令成功退出。
- `pr_summary.md` 包含 pre-existing changes warning。
- `artifact_manifest.json` 中 `baseline_dirty=true`。

## 4. worktree isolation

```bash
codepilot issue --issue-file issue.md --repo . --worktree --approve --overwrite
```

预期结果：
- 命令成功退出。
- 输出包含 `Worktree: enabled`。
- `pr_summary.md` 包含 `Worktree used: yes`。
- 原仓库工作区文件不被 agent 直接改写。

## 5. dirty repo + worktree 不污染 original repo

```bash
codepilot issue --issue-file issue.md --repo . --worktree --dirty-policy fail --approve --overwrite
```

预期结果：
- 命令成功退出。
- 原仓库原有未提交改动仍保留。
- `changes.patch` 来自 effective worktree，不包含原仓库已有脏改动。

## 6. protected .env dirty fail closed

```bash
codepilot issue --issue-file issue.md --repo . --dirty-policy warn --overwrite
```

预期结果：
- 当 `.env` 存在未提交改动时命令非 0 退出。
- 输出包含 `Repo safety denied: protected dirty path detected.`。
- `artifact_manifest.json` 记录 `.env` 在 `protected_dirty_files` 中。
