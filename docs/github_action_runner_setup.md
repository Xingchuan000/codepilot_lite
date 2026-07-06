# Controlled Auto PR GitHub Action Runner 配置

## 1. workflow_dispatch 输入说明

- `issue_url`：GitHub issue URL
- `run_id`：本次运行目录名
- `dry_run`：默认 `"true"`
- `create_pr`：默认 `"false"`

## 2. dry_run=true 默认路径

- 只执行 plan job
- 生成 `auto_pr_plan.md`
- 生成 `auto_pr_manifest.json`
- 生成 `controlled_auto_pr_workflow.yml`
- 不 push
- 不创建 PR

## 3. create_pr=true + dry_run=false 执行路径

- 先执行 plan job
- 再执行 execute job
- execute job 调用 `auto-pr --execute --allow-push --allow-create-pr`

## 4. plan job read-only permissions

- `contents: read`
- `issues: read`
- `pull-requests: read`

## 5. execute job write permissions

- `contents: write`
- `issues: write`
- `pull-requests: write`

## 6. 可选 environment required reviewers

- 可以给 execute job 配置 environment
- 可以通过 required reviewers 做人工批准

## 7. token 权限最小集合

- 使用 `github.token`
- 只需要 `contents: write`
- 只需要 `issues: write`
- 只需要 `pull-requests: write`

## 8. 不支持 issue_comment / pull_request_target 默认触发

- 模板默认不启用 `issue_comment`
- 模板默认不启用 `pull_request_target`

## 9. 不把生成模板自动复制到 .github/workflows

- 生成路径固定在 `runs/<run_id>/controlled_auto_pr_workflow.yml`
- 需要人工复制到仓库 workflow 目录时再自行处理
