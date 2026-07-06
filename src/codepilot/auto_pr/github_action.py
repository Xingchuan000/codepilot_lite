from __future__ import annotations

"""Controlled Auto PR 的 GitHub Action 模板。"""

from pathlib import Path


def render_controlled_auto_pr_workflow_template() -> str:
    """生成只通过 workflow_dispatch 触发的受控模板。"""

    return """name: CodePilot Lite Controlled Auto PR

on:
  workflow_dispatch:
    inputs:
      issue_url:
        description: "GitHub issue URL"
        required: true
      run_id:
        description: "Run ID"
        required: true
      dry_run:
        description: "Plan only"
        required: true
        default: "true"
      create_pr:
        description: "Allow execute job to create PR"
        required: true
        default: "false"

permissions: {}

jobs:
  codepilot-plan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: read
      pull-requests: read
    env:
      CODEPILOT_ISSUE_URL: ${{ inputs.issue_url }}
      CODEPILOT_RUN_ID: ${{ inputs.run_id }}
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install CodePilot Lite
        run: python -m pip install -e .
      - name: Dry-run controlled auto PR
        run: |
          python -m codepilot.cli issue "$CODEPILOT_ISSUE_URL" --repo . --run-id "$CODEPILOT_RUN_ID" --overwrite
          python -m codepilot.cli pr-assist --run-id "$CODEPILOT_RUN_ID" --overwrite --prepare-branch --commit
          python -m codepilot.cli auto-pr --run-id "$CODEPILOT_RUN_ID" --dry-run --controlled-action-template --overwrite
      - name: Upload run artifacts
        uses: actions/upload-artifact@v4
        with:
          name: codepilot-run-${{ inputs.run_id }}
          path: runs/${{ inputs.run_id }}

  codepilot-execute:
    needs: codepilot-plan
    if: ${{ inputs.dry_run != 'true' && inputs.create_pr == 'true' }}
    runs-on: ubuntu-latest
    permissions:
      contents: write
      issues: write
      pull-requests: write
    # environment: controlled-auto-pr
    env:
      CODEPILOT_ISSUE_URL: ${{ inputs.issue_url }}
      CODEPILOT_RUN_ID: ${{ inputs.run_id }}
      GITHUB_TOKEN: ${{ github.token }}
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1
          persist-credentials: true
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install CodePilot Lite
        run: python -m pip install -e .
      - name: Execute controlled auto PR
        run: |
          python -m codepilot.cli issue "$CODEPILOT_ISSUE_URL" --repo . --run-id "$CODEPILOT_RUN_ID" --overwrite
          python -m codepilot.cli pr-assist --run-id "$CODEPILOT_RUN_ID" --overwrite --prepare-branch --commit
          python -m codepilot.cli auto-pr --run-id "$CODEPILOT_RUN_ID" --execute --allow-push --allow-create-pr --token-env GITHUB_TOKEN --controlled-action-template --overwrite
      - name: Upload run artifacts
        uses: actions/upload-artifact@v4
        with:
          name: codepilot-run-${{ inputs.run_id }}-execute
          path: runs/${{ inputs.run_id }}
"""


def write_controlled_auto_pr_workflow_template(output_path: str | Path, *, overwrite: bool = False) -> Path:
    """把模板写到调用者指定路径，不自动写入 .github/workflows。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_controlled_auto_pr_workflow_template(), encoding="utf-8")
    return path
