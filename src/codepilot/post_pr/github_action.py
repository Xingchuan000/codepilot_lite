from __future__ import annotations

"""第十五步 Post-PR automation 的 GitHub Action workflow 模板。"""

from pathlib import Path


_WORKFLOW_TEMPLATE = """name: CodePilot Lite Post PR Automation

on:
  workflow_dispatch:
    inputs:
      run_id:
        required: true
      max_rounds:
        required: true
        default: "2"
      wait_ci:
        required: true
        default: "false"
      execute:
        required: true
        default: "false"
      approve_run_agent:
        required: true
        default: "false"
      approve_push_update:
        required: true
        default: "false"
      approve_comment:
        required: true
        default: "false"
      confirm_execute:
        required: false
        description: "Type I_APPROVE_CODEPILOT_POST_PR_AUTOMATION to allow execute job"

permissions: {}

concurrency:
  group: codepilot-post-pr-${{ inputs.run_id }}
  cancel-in-progress: false

jobs:
  plan:
    permissions:
      contents: read
      pull-requests: read
      checks: read
      actions: read
    runs-on: ubuntu-latest
    env:
      INPUT_RUN_ID: ${{ inputs.run_id }}
      INPUT_MAX_ROUNDS: ${{ inputs.max_rounds }}
      INPUT_WAIT_CI: ${{ inputs.wait_ci }}
      INPUT_EXECUTE: ${{ inputs.execute }}
      INPUT_APPROVE_RUN_AGENT: ${{ inputs.approve_run_agent }}
      INPUT_APPROVE_PUSH_UPDATE: ${{ inputs.approve_push_update }}
      INPUT_APPROVE_COMMENT: ${{ inputs.approve_comment }}
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install package
        run: python -m pip install -e .
      - name: Plan post-pr automation
        run: |
          set -eu
          case "$INPUT_MAX_ROUNDS" in ''|*[!0-9]*) echo "invalid max_rounds" >&2; exit 2 ;; esac
          case "$INPUT_WAIT_CI" in true|false) ;; *) echo "invalid wait_ci" >&2; exit 2 ;; esac
          case "$INPUT_EXECUTE" in true|false) ;; *) echo "invalid execute" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_RUN_AGENT" in true|false) ;; *) echo "invalid approve_run_agent" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_PUSH_UPDATE" in true|false) ;; *) echo "invalid approve_push_update" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_COMMENT" in true|false) ;; *) echo "invalid approve_comment" >&2; exit 2 ;; esac
          args=(python -m codepilot.cli post-pr --run-id "$INPUT_RUN_ID" --dry-run --overwrite --max-rounds "$INPUT_MAX_ROUNDS")
          if [ "$INPUT_WAIT_CI" = "true" ]; then
            args+=(--wait-ci)
          fi
          if [ "$INPUT_APPROVE_COMMENT" = "true" ]; then
            args+=(--approve-comment)
          fi
          PYTHONPATH=src "${args[@]}"
      - name: Upload post-pr artifacts
        uses: actions/upload-artifact@v4
        with:
          name: codepilot-post-pr-${{ inputs.run_id }}-plan
          path: runs/${{ inputs.run_id }}/post_pr/**

  execute-update:
    needs: plan
    if: ${{ inputs.execute == 'true' && inputs.confirm_execute == 'I_APPROVE_CODEPILOT_POST_PR_AUTOMATION' && inputs.approve_comment != 'true' }}
    runs-on: ubuntu-latest
    environment: codepilot-post-pr-approval
    permissions:
      contents: write
      pull-requests: write
      checks: read
      actions: read
    env:
      INPUT_RUN_ID: ${{ inputs.run_id }}
      INPUT_MAX_ROUNDS: ${{ inputs.max_rounds }}
      INPUT_WAIT_CI: ${{ inputs.wait_ci }}
      INPUT_APPROVE_RUN_AGENT: ${{ inputs.approve_run_agent }}
      INPUT_APPROVE_PUSH_UPDATE: ${{ inputs.approve_push_update }}
      INPUT_APPROVE_COMMENT: ${{ inputs.approve_comment }}
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install package
        run: python -m pip install -e .
      - uses: actions/download-artifact@v4
        with:
          name: codepilot-post-pr-${{ inputs.run_id }}-plan
          path: runs/${{ inputs.run_id }}/post_pr
      - name: Execute post-pr automation
        run: |
          set -eu
          case "$INPUT_MAX_ROUNDS" in ''|*[!0-9]*) echo "invalid max_rounds" >&2; exit 2 ;; esac
          case "$INPUT_WAIT_CI" in true|false) ;; *) echo "invalid wait_ci" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_RUN_AGENT" in true|false) ;; *) echo "invalid approve_run_agent" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_PUSH_UPDATE" in true|false) ;; *) echo "invalid approve_push_update" >&2; exit 2 ;; esac
          args=(python -m codepilot.cli post-pr --run-id "$INPUT_RUN_ID" --execute --resume --overwrite --max-rounds "$INPUT_MAX_ROUNDS")
          if [ "$INPUT_WAIT_CI" = "true" ]; then
            args+=(--wait-ci)
          fi
          if [ "$INPUT_APPROVE_RUN_AGENT" = "true" ]; then
            args+=(--approve-run-agent)
          fi
          if [ "$INPUT_APPROVE_PUSH_UPDATE" = "true" ]; then
            args+=(--approve-push-update)
          fi
          PYTHONPATH=src "${args[@]}"

  execute-update-with-comment:
    needs: plan
    if: ${{ inputs.execute == 'true' && inputs.confirm_execute == 'I_APPROVE_CODEPILOT_POST_PR_AUTOMATION' && inputs.approve_comment == 'true' }}
    runs-on: ubuntu-latest
    environment: codepilot-post-pr-approval
    permissions:
      contents: write
      pull-requests: write
      issues: write
      checks: read
      actions: read
    env:
      INPUT_RUN_ID: ${{ inputs.run_id }}
      INPUT_MAX_ROUNDS: ${{ inputs.max_rounds }}
      INPUT_WAIT_CI: ${{ inputs.wait_ci }}
      INPUT_APPROVE_RUN_AGENT: ${{ inputs.approve_run_agent }}
      INPUT_APPROVE_PUSH_UPDATE: ${{ inputs.approve_push_update }}
      INPUT_APPROVE_COMMENT: ${{ inputs.approve_comment }}
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install package
        run: python -m pip install -e .
      - uses: actions/download-artifact@v4
        with:
          name: codepilot-post-pr-${{ inputs.run_id }}-plan
          path: runs/${{ inputs.run_id }}/post_pr
      - name: Execute post-pr automation with comment
        run: |
          set -eu
          case "$INPUT_MAX_ROUNDS" in ''|*[!0-9]*) echo "invalid max_rounds" >&2; exit 2 ;; esac
          case "$INPUT_WAIT_CI" in true|false) ;; *) echo "invalid wait_ci" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_RUN_AGENT" in true|false) ;; *) echo "invalid approve_run_agent" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_PUSH_UPDATE" in true|false) ;; *) echo "invalid approve_push_update" >&2; exit 2 ;; esac
          case "$INPUT_APPROVE_COMMENT" in true|false) ;; *) echo "invalid approve_comment" >&2; exit 2 ;; esac
          args=(python -m codepilot.cli post-pr --run-id "$INPUT_RUN_ID" --execute --resume --overwrite --max-rounds "$INPUT_MAX_ROUNDS")
          if [ "$INPUT_WAIT_CI" = "true" ]; then
            args+=(--wait-ci)
          fi
          if [ "$INPUT_APPROVE_RUN_AGENT" = "true" ]; then
            args+=(--approve-run-agent)
          fi
          if [ "$INPUT_APPROVE_PUSH_UPDATE" = "true" ]; then
            args+=(--approve-push-update)
          fi
          if [ "$INPUT_APPROVE_COMMENT" = "true" ]; then
            args+=(--approve-comment)
          fi
          PYTHONPATH=src "${args[@]}"
"""


def render_post_pr_automation_workflow_template() -> str:
    return _WORKFLOW_TEMPLATE


def write_post_pr_automation_workflow_template(output_path: str | Path, *, overwrite: bool = False) -> Path:
    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_post_pr_automation_workflow_template(), encoding="utf-8")
    return path
