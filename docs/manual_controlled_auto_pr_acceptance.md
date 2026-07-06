# Controlled Auto PR 手动验收

## 1. dry-run plan 验收

```bash
PYTHONPATH=src python -m codepilot.cli auto-pr \
  --run-dir runs/<run_id> \
  --repo-slug owner/repo \
  --overwrite
```

预期：
- 生成 `runs/<run_id>/auto_pr_plan.md`
- 生成 `runs/<run_id>/auto_pr_manifest.json`
- 生成 `runs/<run_id>/controlled_auto_pr_workflow.yml`
- 输出 `Push executed: no`
- 输出 `PR created: no`

## 2. safety fail execute blocked 验收

```bash
PYTHONPATH=src python -m codepilot.cli auto-pr \
  --run-dir runs/<run_id> \
  --repo-slug owner/repo \
  --execute \
  --allow-push \
  --overwrite
```

预期：
- 返回非 0
- 输出 `Status: blocked_by_safety`
- 不执行 push
- 不创建 PR

## 3. execute 但未 allow-push 验收

```bash
PYTHONPATH=src python -m codepilot.cli auto-pr \
  --run-dir runs/<run_id> \
  --repo-slug owner/repo \
  --execute \
  --overwrite
```

预期：
- 返回非 0
- 不执行 push
- 不创建 PR

## 4. 本地 bare remote push 验收

```bash
PYTHONPATH=src pytest tests/codepilot/test_auto_pr_git_push.py -q
```

预期：
- `execute=True + allow_push=True` 能推送到临时 bare remote
- push 后远端分支 sha 与本地 commit sha 一致

## 5. FakeGitHubClient 单元测试验收

```bash
PYTHONPATH=src pytest tests/codepilot/test_auto_pr_github_client.py tests/codepilot/test_auto_pr_pr_creator.py -q
```

预期：
- 不访问真实 GitHub
- fake PR URL 为 `https://github.com/<owner>/<repo>/pull/123`

## 6. 真实 GitHub 临时 repo smoke test 验收

```bash
export GITHUB_TOKEN=...
PYTHONPATH=src python -m codepilot.cli auto-pr \
  --run-dir runs/<run_id> \
  --repo-slug <owner>/<repo> \
  --execute \
  --allow-push \
  --allow-create-pr \
  --token-env GITHUB_TOKEN \
  --overwrite
```

预期：
- 推送 `codepilot/<run_id>` 分支
- 创建 draft PR
- `auto_pr_manifest.json` 不包含 token 值

## 7. workflow 模板生成验收

```bash
PYTHONPATH=src pytest tests/codepilot/test_auto_pr_github_action.py -q
```

预期：
- 模板只包含 `workflow_dispatch`
- 顶层 `permissions: {}`
- plan job 只读
- execute job 只在 `dry_run=false && create_pr=true` 时执行
