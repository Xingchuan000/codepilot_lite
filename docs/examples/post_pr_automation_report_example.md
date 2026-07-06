# Post-PR Automation Report Example

以下示例只使用假数据，不包含真实 token、真实日志或真实 diff。

```md
# CodePilot Post-PR Automation Report

## Run / PR Context

- Run ID: issue-001
- Run Dir: runs/issue-001
- Post-PR Dir: runs/issue-001/post_pr

## Automation Mode

- Status: awaiting_approval
- Terminal Reason: awaiting_approval
- Rounds: 1

## Round Timeline

| round | phase | status | terminal reason | fingerprints |
| --- | --- | --- | --- | --- |
| round-001 | collect | feedback_found | none | fp-1, fp-2 |

## Approval Scope

- Approval request: post_pr/approval_request.md
- Approval decision: n/a

## Side Effects Ledger Summary

- Side effects: post_pr/side_effects.json

## Manual Next Command

codepilot post-pr --run-dir runs/issue-001 --execute --approval-file post_pr/approval_decision.json --resume
```

