# Post-PR Human Approval Gate Setup

`approval_decision.json` 的最小结构如下：

```json
{
  "schema_version": "codepilot.post_pr.approval_decision.v1",
  "run_id": "issue-001",
  "round_id": "round-001",
  "status": "approved",
  "approved_actions": ["run_agent"],
  "head_sha": "abc123",
  "auto_pr_manifest_sha256": "....",
  "pr_feedback_manifest_sha256": "....",
  "approval_request_sha256": "....",
  "reason": "Reviewed artifacts and approve running agent only."
}
```

使用方式：

```bash
codepilot post-pr --run-dir runs/<run_id> --execute --approval-file runs/<run_id>/post_pr/approval_decision.json --resume
```

建议只在以下情况批准：

- `run_id` 与当前 run 一致
- `round_id` 与当前审批轮一致
- `head_sha` 与当前 PR head 一致
- `auto_pr_manifest_sha256`、`pr_feedback_manifest_sha256`、`approval_request_sha256` 全部匹配

如果任一字段不一致，当前审批会被视为过期或不安全。

