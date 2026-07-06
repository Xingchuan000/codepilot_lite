# Manual PR Assist Acceptance

## A. Clean repo run 生成 PR assist artifacts

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-clean \
  --strict-safety \
  --github-action-template \
  --overwrite
```

预期：

- 生成 `pr_body.md`
- 生成 `manual_pr_commands.md`
- 生成 `review_checklist.md`
- 生成 `github_action_template.yml`
- 生成 `pr_assist_manifest.json`

## B. Safety fail run 只能生成 review-only artifacts

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-safety-fail \
  --strict-safety \
  --overwrite
```

预期：

- 仍生成 `pr_body.md`
- 仍生成 `manual_pr_commands.md`
- 仍生成 `review_checklist.md`
- `manual_pr_commands.md` 不包含 `git apply --check`
- `manual_pr_commands.md` 不包含 `git apply --index`
- `manual_pr_commands.md` 不包含 `git commit`

## C. include gh pr command 只生成注释命令，不调用 GitHub API

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-clean \
  --include-gh-pr-command \
  --overwrite
```

预期：

- `manual_pr_commands.md` 包含 `# gh pr create`
- 不会执行 `gh pr create`
- 不会调用 GitHub API

## D. prepare branch 只创建本地 branch，不 push、不 upstream

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-clean \
  --prepare-branch \
  --branch-prefix codepilot \
  --overwrite
```

预期：

- 创建本地分支 `codepilot/<safe-run-id>`
- 不执行 push
- 不设置 upstream

## E. commit 只 stage patch metadata changed_files，不 stage runs artifacts

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-clean \
  --prepare-branch \
  --commit \
  --overwrite
```

预期：

- 只暂存 patch metadata 中的 `changed_files`
- 不执行 `git add .`
- 不提交 `runs/<run_id>/`
- 不提交受保护路径
- 不执行 push

## F. GitHub Action 模板生成在 runs/<run_id>/，不写 .github/workflows

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-clean \
  --github-action-template \
  --overwrite
```

预期：

- `runs/<run_id>/github_action_template.yml` 存在
- `.github/workflows/` 没有新文件

## G. manifest invalid 返回可读错误

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-bad-manifest
```

预期：

- 退出码非 0
- 错误信息可读
- 会生成 `pr_assist_manifest.json` 记录 `manifest_invalid`

## H. overwrite 行为只覆盖 PR assist artifacts，不破坏第十一步 artifacts

```bash
PYTHONPATH=src python -m codepilot.cli pr-assist \
  --run-dir runs/issue-hardening-clean \
  --overwrite
```

预期：

- 旧的 `pr_body.md`、`manual_pr_commands.md`、`review_checklist.md`、`github_action_template.yml`、`pr_assist_manifest.json` 被覆盖
- `changes.patch`、`report.json`、`artifact_manifest.json` 保留
