from __future__ import annotations

"""渲染第十四步 PR feedback GitHub Action workflow。"""

from pathlib import Path


WORKFLOW_TEMPLATE = """name: CodePilot Lite PR Feedback

on:
  workflow_dispatch:
    inputs:
      run_id:
        required: true
      pull_number:
        required: false
      repo_slug:
        required: false
      head_branch:
        required: false
      dry_run:
        required: true
        default: "true"
      execute:
        required: true
        default: "false"
      wait_ci:
        required: true
        default: "false"
      include_logs:
        required: true
        default: "true"
      max_log_bytes:
        required: true
        default: "200000"
      max_feedback_items:
        required: true
        default: "20"
      allow_run_agent:
        required: true
        default: "false"
      allow_push_update:
        required: true
        default: "false"
      allow_comment:
        required: true
        default: "false"
      artifact_path:
        required: false

permissions: {}

jobs:
  feedback-plan:
    permissions:
      contents: read
      pull-requests: read
      checks: read
      actions: read
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install package
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e .
      - name: Run PR feedback dry-run
        env:
          CODEPILOT_RUN_ID: ${{ inputs.run_id }}
          CODEPILOT_PULL_NUMBER: ${{ inputs.pull_number }}
          CODEPILOT_REPO_SLUG: ${{ inputs.repo_slug }}
          CODEPILOT_HEAD_BRANCH: ${{ inputs.head_branch }}
          CODEPILOT_INCLUDE_LOGS: ${{ inputs.include_logs }}
          CODEPILOT_MAX_LOG_BYTES: ${{ inputs.max_log_bytes }}
          CODEPILOT_MAX_FEEDBACK_ITEMS: ${{ inputs.max_feedback_items }}
          CODEPILOT_WAIT_CI: ${{ inputs.wait_ci }}
          CODEPILOT_ARTIFACT_PATH: ${{ inputs.artifact_path }}
        run: |
          set -eu
          case "$CODEPILOT_INCLUDE_LOGS" in true|false) ;; *) echo "invalid include_logs" >&2; exit 2 ;; esac
          case "$CODEPILOT_WAIT_CI" in true|false) ;; *) echo "invalid wait_ci" >&2; exit 2 ;; esac
          case "$CODEPILOT_MAX_LOG_BYTES" in ''|*[!0-9]*) echo "invalid max_log_bytes" >&2; exit 2 ;; esac
          case "$CODEPILOT_MAX_FEEDBACK_ITEMS" in ''|*[!0-9]*) echo "invalid max_feedback_items" >&2; exit 2 ;; esac
          if [ -n "$CODEPILOT_PULL_NUMBER" ]; then
            case "$CODEPILOT_PULL_NUMBER" in *[!0-9]*) echo "invalid pull_number" >&2; exit 2 ;; esac
          fi
          args=(python -m codepilot.cli pr-feedback --run-id "$CODEPILOT_RUN_ID" --dry-run --overwrite)
          if [ -n "$CODEPILOT_PULL_NUMBER" ]; then
            args+=(--pull-number "$CODEPILOT_PULL_NUMBER")
          fi
          if [ -n "$CODEPILOT_REPO_SLUG" ]; then
            args+=(--repo-slug "$CODEPILOT_REPO_SLUG")
          fi
          if [ -n "$CODEPILOT_HEAD_BRANCH" ]; then
            args+=(--head-branch "$CODEPILOT_HEAD_BRANCH")
          fi
          if [ "$CODEPILOT_INCLUDE_LOGS" = "false" ]; then
            args+=(--no-include-logs)
          fi
          if [ "$CODEPILOT_WAIT_CI" = "true" ]; then
            args+=(--wait-ci)
          fi
          args+=(--max-log-bytes "$CODEPILOT_MAX_LOG_BYTES" --max-feedback-items "$CODEPILOT_MAX_FEEDBACK_ITEMS")
          PYTHONPATH=src "${args[@]}"
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: pr-feedback-plan
          path: |
            runs/${{ inputs.run_id }}/ci_status.json
            runs/${{ inputs.run_id }}/review_feedback.json
            runs/${{ inputs.run_id }}/ci_feedback_report.md
            runs/${{ inputs.run_id }}/followup_task.md
            runs/${{ inputs.run_id }}/pr_update_plan.md
            runs/${{ inputs.run_id }}/ci_feedback_manifest.json
            runs/${{ inputs.run_id }}/pr_feedback_workflow.yml
            runs/${{ inputs.run_id }}/ci_logs/*.summary.md

  execute-update:
    needs: feedback-plan
    if: ${{ inputs.execute == 'true' && inputs.allow_run_agent == 'true' && inputs.allow_push_update == 'true' }}
    permissions:
      contents: write
      pull-requests: write
      checks: read
      actions: read
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install package
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e .
      - uses: actions/download-artifact@v4
        with:
          name: pr-feedback-plan
      - name: Run PR feedback execute
        env:
          CODEPILOT_RUN_ID: ${{ inputs.run_id }}
          CODEPILOT_PULL_NUMBER: ${{ inputs.pull_number }}
          CODEPILOT_REPO_SLUG: ${{ inputs.repo_slug }}
          CODEPILOT_HEAD_BRANCH: ${{ inputs.head_branch }}
          CODEPILOT_ALLOW_COMMENT: ${{ inputs.allow_comment }}
        run: |
          set -eu
          case "$CODEPILOT_ALLOW_COMMENT" in true|false) ;; *) echo "invalid allow_comment" >&2; exit 2 ;; esac
          if [ -n "$CODEPILOT_PULL_NUMBER" ]; then
            case "$CODEPILOT_PULL_NUMBER" in *[!0-9]*) echo "invalid pull_number" >&2; exit 2 ;; esac
          fi
          args=(python -m codepilot.cli pr-feedback --run-id "$CODEPILOT_RUN_ID" --execute --allow-run-agent --allow-push-update --overwrite)
          if [ -n "$CODEPILOT_PULL_NUMBER" ]; then
            args+=(--pull-number "$CODEPILOT_PULL_NUMBER")
          fi
          if [ -n "$CODEPILOT_REPO_SLUG" ]; then
            args+=(--repo-slug "$CODEPILOT_REPO_SLUG")
          fi
          if [ -n "$CODEPILOT_HEAD_BRANCH" ]; then
            args+=(--head-branch "$CODEPILOT_HEAD_BRANCH")
          fi
          if [ "$CODEPILOT_ALLOW_COMMENT" = "true" ]; then
            args+=(--allow-comment)
          fi
          PYTHONPATH=src "${args[@]}"
"""


def render_pr_feedback_workflow_template() -> str:
    """返回固定的 workflow 模板文本。"""

    return WORKFLOW_TEMPLATE


def write_pr_feedback_workflow_template(output_path: str | Path, *, overwrite: bool = False) -> Path:
    """把 workflow 模板写入 run_dir，不自动写到仓库 workflow 目录。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_pr_feedback_workflow_template(), encoding="utf-8")
    return path
