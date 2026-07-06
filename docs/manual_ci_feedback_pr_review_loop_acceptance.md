# 第14步 CI Feedback / PR Review Loop 手动验收

## A. 缺少 manifest 的失败路径

```bash
PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<empty-run> --overwrite
```

## B. 合法 auto_pr_manifest 但缺少 GITHUB_TOKEN 的 dry-run degraded 路径

```bash
unset GITHUB_TOKEN
PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run-id> --dry-run --overwrite
```

## C. 真实 GitHub read-only dry-run

```bash
GITHUB_TOKEN=... PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run-id> --dry-run --overwrite
```

## D. 等待 CI

```bash
GITHUB_TOKEN=... PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run-id> --dry-run --wait-ci --timeout-seconds 300 --overwrite
```

## E. execute 但不允许 run-agent

```bash
GITHUB_TOKEN=... PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run-id> --execute --overwrite
```

## F. execute + allow-run-agent，但不 push

```bash
GITHUB_TOKEN=... PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run-id> --execute --allow-run-agent --overwrite
```

## G. execute + allow-run-agent + allow-push-update

```bash
GITHUB_TOKEN=... PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run-id> --execute --allow-run-agent --allow-push-update --overwrite
```

## 验收产物

- `ci_status.json`
- `review_feedback.json`
- `ci_feedback_report.md`
- `followup_task.md`
- `pr_update_plan.md`
- `ci_feedback_manifest.json`
- `pr_feedback_workflow.yml`
- `followup/attempt-001/`

## 安全预期

1. 默认 dry-run 不运行 agent。
2. 默认 dry-run 不 push。
3. execute 没有 `--allow-run-agent` 时不运行 agent。
4. execute 没有 `--allow-push-update` 时不 push。
5. stale head 阻止 execute。
6. workflow 模板不包含 `eval`。
7. workflow 不使用 `pull_request_target`。
8. artifact 中不包含 token、env、full prompt、full logs、full diff。
