# Manual Bounded Post-PR Automation Acceptance

1. `codepilot post-pr --run-dir runs/<run_id> --overwrite`
   - 预期生成 `post_pr/state.json`
   - 预期生成 `post_pr/approval_request.md`
   - 预期生成 `post_pr/post_pr_automation_report.md`

2. `codepilot post-pr --run-dir runs/<run_id> --execute`
   - 没有审批时只停在 `awaiting_approval`
   - 不运行 follow-up agent
   - 不 push
   - 不 comment

3. `codepilot post-pr --run-dir runs/<run_id> --execute --approve-run-agent --resume`
   - 允许生成 patch / commit
   - 不会自动 push，除非还批准 `--approve-push-update`

4. `codepilot post-pr --run-dir runs/<run_id> --execute --approve-run-agent --approve-push-update --resume`
   - 允许进入下一轮 collect
   - 如果反馈没有变化，会按 repeated feedback 停止

5. `codepilot post-pr --run-dir runs/<run_id> --execute --resume --approval-file post_pr/approval_decision.json`
   - 只接受与 `run_id`、`round_id`、`head_sha` 和 manifest hash 绑定的审批
   - 过期审批直接拒绝

6. `codepilot post-pr --run-dir runs/<run_id> --execute --resume`
   - `resume` 不会重复执行已经成功的 side effect

7. 所有产物只检查 `post_pr/` 目录
   - 不删除 `auto_pr_manifest.json`
   - 不删除 `pr_assist_manifest.json`
   - 不删除 `artifact_manifest.json`

