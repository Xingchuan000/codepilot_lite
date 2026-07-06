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
      dry_run:
        required: true
        default: "true"
      wait_ci:
        required: true
        default: "false"
      follow_up:
        required: true
        default: "false"
      update_pr:
        required: true
        default: "false"

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
      - name: Run PR feedback dry-run
        run: |
          PYTHONPATH=src python -m codepilot.cli pr-feedback --run-id "${{ inputs.run_id }}" --dry-run --overwrite
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: pr-feedback-plan
          path: runs/${{ inputs.run_id }}/

  execute-update:
    needs: feedback-plan
    if: ${{ inputs.follow_up == 'true' && inputs.update_pr == 'true' }}
    permissions:
      contents: write
      pull-requests: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/download-artifact@v4
        with:
          name: pr-feedback-plan
      - name: Run PR feedback execute
        run: |
          PYTHONPATH=src python -m codepilot.cli pr-feedback --run-id "${{ inputs.run_id }}" --execute --allow-run-agent --allow-push-update --overwrite
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
