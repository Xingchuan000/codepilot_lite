<div align="center">
<a href="https://mini-swe-agent.com/latest/"><img src="https://github.com/SWE-agent/mini-swe-agent/raw/main/docs/assets/mini-swe-agent-banner.svg" alt="mini-swe-agent banner" style="height: 7em"/></a>
</div>

# The minimal AI software engineering agent

📣 [mini-swe-agent now powers Ramp SWE-Bench](https://labs.ramp.com/swebench)<br/>
📣 [mini-swe-agent beats Claude Code and Codex on DeepSWE](https://deepswe.datacurve.ai/blog#evaluation-harness)<br/>
📣 [Run mini-swe-agent on our new & extremely challenging benchmark, ProgramBench](https://mini-swe-agent.com/latest/usage/programbench/)<br/>
📣 [New tutorial on building minimal AI agents](https://minimal-agent.com/)

[![Docs](https://img.shields.io/badge/Docs-green?style=for-the-badge&logo=materialformkdocs&logoColor=white)](https://mini-swe-agent.com/latest/)
[![Slack](https://img.shields.io/badge/Slack-4A154B?style=for-the-badge&logo=slack&logoColor=white)](https://join.slack.com/t/swe-bench/shared_invite/zt-36pj9bu5s-o3_yXPZbaH2wVnxnss1EkQ)
[![PyPI - Version](https://img.shields.io/pypi/v/mini-swe-agent?style=for-the-badge&logo=python&logoColor=white&labelColor=black&color=deeppink)](https://pypi.org/project/mini-swe-agent/)

> [!WARNING]
> This is **mini-swe-agent v2**. Read the [migration guide](https://mini-swe-agent.com/latest/advanced/v2_migration/). For the previous version, check out the [v1 branch](https://github.com/SWE-agent/mini-swe-agent/tree/v1).

In 2024, we built [SWE-bench](https://github.com/swe-bench/SWE-bench) & [SWE-agent](https://github.com/swe-agent/swe-agent) and helped kickstart the coding agent revolution.

We now ask: **What if our agent was 100x simpler, and still worked nearly as well?**

`mini` is

- **Widely adopted**: Used by Meta, NVIDIA, Essential AI, IBM, Nebius, Anyscale, Princeton University, Stanford University, and many more.
- **Minimal**: Just some 100 lines of python for the [agent class](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py) (and a bit more for the [environment](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/environments/local.py),
[model](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/models/litellm_model.py), and [run script](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/run/hello_world.py)) — no fancy dependencies!
- **Performant:** Scores >74% on the [SWE-bench verified benchmark](https://www.swebench.com/); starts much faster than Claude Code
- **Deployable:** Supports **local environments**, **docker/podman**, **singularity/apptainer**, **bublewrap**, **contree**, and more
- **Compatible:** Supports all models via **litellm**, **openrouter**, **portkey**, and more. Support for `/completion` and `/response` endpoints, interleaved thinking etc.
- Built by the Princeton & Stanford team behind [SWE-bench](https://swebench.com), [SWE-agent](https://swe-agent.com), and more
- **Tested:** [![Codecov](https://img.shields.io/codecov/c/github/swe-agent/mini-swe-agent?style=flat-square)](https://codecov.io/gh/SWE-agent/mini-swe-agent)

<details>

<summary>More motivation (for research)</summary>

[SWE-agent](https://swe-agent.com/latest/) jump-started the development of AI agents in 2024. Back then, we placed a lot of emphasis on tools and special interfaces for the agent.
However, one year later, as LMs have become more capable, a lot of this is not needed at all to build a useful agent!
In fact, the `mini` agent

- **Does not have any tools other than bash** — it doesn't even need to use the tool-calling interface of the LMs.
  This means that you can run it with literally any model. When running in sandboxed environments you also don't need to take care
  of installing a single package — all it needs is bash.
- **Has a completely linear history** — every step of the agent just appends to the messages and that's it.
  So there's no difference between the trajectory and the messages that you pass on to the LM.
  Great for debugging & fine-tuning.
- **Executes actions with `subprocess.run`** — every action is completely independent (as opposed to keeping a stateful shell session running).
  This makes it trivial to execute the actions in sandboxes (literally just switch out `subprocess.run` with `docker exec`) and to
  scale up effortlessly. Seriously, this is [a big deal](https://mini-swe-agent.com/latest/faq/#why-no-shell-session), trust me.

This makes it perfect as a baseline system and for a system that puts the language model (rather than
the agent scaffold) in the middle of our attention.
You can see the result on the [SWE-bench (bash only)](https://www.swebench.com/) leaderboard, that evaluates the performance of different LMs with `mini`.

</details>

<details>
<summary>More motivation (as a tool)</summary>

Some agents are overfitted research artifacts. Others are UI-heavy frontend monsters.

The `mini` agent wants to be a hackable tool, not a black box.

- **Simple** enough to understand at a glance
- **Convenient** enough to use in daily workflows
- **Flexible** to extend

Unlike other agents (including our own [swe-agent](https://swe-agent.com/latest/)), it is radically simpler, because it:

- **Does not have any tools other than bash** — it doesn't even need to use the tool-calling interface of the LMs.
  Instead of implementing custom tools for every specific thing the agent might want to do, the focus is fully on the LM utilizing the shell to its full potential.
  Want it to do something specific like opening a PR?
  Just tell the LM to figure it out rather than spending time to implement it in the agent.
- **Executes actions with `subprocess.run`** — every action is completely independent (as opposed to keeping a stateful shell session running).
  This is [a big deal](https://mini-swe-agent.com/latest/faq/#why-no-shell-session) for the stability of the agent, trust me.
- **Has a completely linear history** — every step of the agent just appends to the messages that are passed to the LM in the next step and that's it.
  This is great for debugging and understanding what the LM is prompted with.

</details>

<details>
<summary>Should I use SWE-agent or mini-SWE-agent?</summary>

You should consider `mini-swe-agent` your default choice.
In particular, you should use `mini-swe-agent` if

- You want a quick command line tool that works locally
- You want an agent with a very simple control flow
- You want even faster, simpler & more stable sandboxing & benchmark evaluations
- You are doing FT or RL and don't want to overfit to a specific agent scaffold

You should use `swe-agent` if

- You want to experiment with different sets of tools, each with their own interface
- You want to experiment with different history processors

What you get with both

- Excellent performance on SWE-Bench
- A trajectory browser

</details>

<table>
<tr>
<td width="50%">
<a href="https://mini-swe-agent.com/latest/usage/mini/"><strong>CLI</strong></a> (<code>mini</code>)
</td>
<td>
<a href="https://mini-swe-agent.com/latest/usage/swebench/"><strong>Batch inference</strong></a>
</td>
</tr>
<tr>
<td width="50%">

![mini](https://github.com/SWE-agent/swe-agent-media/blob/main/media/mini/gif/mini.gif?raw=true)

</td>
<td>

![swebench](https://github.com/SWE-agent/swe-agent-media/blob/main/media/mini/gif/swebench.gif?raw=true)

</td>
</tr>
<tr>
<td>
<a href="https://mini-swe-agent.com/latest/usage/inspector/"><strong>Trajectory browser</strong></a>
</td>
<td>
<a href="https://mini-swe-agent.com/latest/advanced/cookbook/"><strong>Python bindings</strong></a>
</td>
</tr>
<tr>
<td>

![inspector](https://github.com/SWE-agent/swe-agent-media/blob/main/media/mini/gif/inspector.gif?raw=true)

</td>
<td>

```python
agent = DefaultAgent(
    LitellmModel(model_name=...),
    LocalEnvironment(),
)
agent.run("Write a sudoku game")
```

</td>
</tr>
</table>

## Let's get started!

**Option 1:** If you just want to try out the CLI (package installed in anonymous virtual environment)

```bash
pip install uv && uvx mini-swe-agent
# or
pip install pipx && pipx ensurepath && pipx run mini-swe-agent
```

**Option 2:** Install CLI & python bindings in current environment

```bash
pip install mini-swe-agent
mini  # run the CLI
```

**Option 3:** Install from source (developer setup)

```bash
git clone https://github.com/SWE-agent/mini-swe-agent.git
cd mini-swe-agent && pip install -e .
mini  # run the CLI
```

Read more in our [documentation](https://mini-swe-agent.com/latest/):

* [Quick start guide](https://mini-swe-agent.com/latest/quickstart/)
* [Using the `mini` CLI](https://mini-swe-agent.com/latest/usage/mini/)
* [Global configuration](https://mini-swe-agent.com/latest/advanced/global_configuration/)
* [Yaml configuration files](https://mini-swe-agent.com/latest/advanced/yaml_configuration/)
* [Power up with the cookbook](https://mini-swe-agent.com/latest/advanced/cookbook/)
* [FAQ](https://mini-swe-agent.com/latest/faq/)
* [Contribute!](https://mini-swe-agent.com/latest/contributing/)

## CodePilot Lite 第二步 — 结构化工具层

CodePilot Lite 第二步实现了一个独立的结构化工具层，提供四个核心工具：`list_files`、`read_file`、`search_code` 和 `run_shell`。
当前阶段**不接入 LLM**，工具只通过 CLI 以结构化 JSON 暴露能力。

### 工具概览

| 工具 | 风险等级 | 默认权限 | 说明 |
|------|----------|----------|------|
| `list_files` | read_only | allow | 列出仓库目录树 |
| `read_file` | read_only | allow | 读取文件片段并保留行号 |
| `search_code` | read_only | allow | 在源码中搜索关键词 |
| `run_shell` | shell_execution | ask | 在仓库根目录执行 shell 命令（高风险 fallback） |

每个工具都返回统一的 `ToolResult`，包含 `success`、`output`、`output_summary`、`error` 和 `metadata` 字段。
`metadata` 中始终携带 `risk`、`duration_ms`、工具特有参数以及 `truncated` 截断标记。

### 使用示例

```bash
# 查看所有工具
codepilot tools

# 列表目录
codepilot tool list_files '{"repo":".","path":".","max_depth":2}'

# 读取文件片段
codepilot tool read_file '{"repo":".","path":"src/codepilot/tools/base.py","start_line":1,"end_line":80}'

# 搜索代码
codepilot tool search_code '{"repo":".","query":"ToolResult","path":"src/codepilot"}'

# 执行 shell 命令
codepilot tool run_shell '{"repo":".","command":"python --version"}'
```

`tool` 子命令接收一个 JSON 字符串作为参数，输出为带缩进的 `ToolResult` JSON（indent=2），适合人工调试。
其中 `run_shell` 是唯一高风险工具，默认标记为 `ask` 权限，30 秒超时。  
`search_code` 在结果截断时会在 output 末尾追加 `... truncated after N results` 提示。  
`list_files` 严格按 `max_depth` 控制返回层级深度，不会越界。

### TraceLogger 使用说明

第三步新增了结构化 trace 记录能力。默认 trace 文件会写到 `runs/<run_id>/trace.jsonl`，每一行都是一条 JSON 事件。

```bash
# 仅执行工具调用，不写 trace
codepilot tool list_files '{"repo":".","path":".","max_depth":2}'

# 写入 trace，默认输出到 runs/<run_id>/trace.jsonl
codepilot tool list_files '{"repo":".","path":".","max_depth":2}' --trace
```

### Evidence Report 使用说明

第九步新增了 `codepilot report` 命令，用来把已有的 `trace.jsonl` 转成可阅读的 `report.md`。
这个命令只读取 trace，不会重新调用 LLM，也不会重新执行任何工具。

```bash
# 直接指定 trace 文件
codepilot report --trace runs/<run_id>/trace.jsonl --overwrite

# 通过 run_id 自动定位到 runs/<run_id>/trace.jsonl
codepilot report --run-id <run_id> --runs-dir runs --overwrite

# 额外输出 report.json
codepilot report --trace runs/<run_id>/trace.jsonl --json --overwrite
```

默认输出文件是 `trace.jsonl` 同目录下的 `report.md`。  
如果希望写到别的位置，可以加 `--output <path>`。  
如果目标 `report.md` 已存在，需要显式加 `--overwrite` 才会覆盖。

### Controlled Auto PR 使用说明

第十三步新增了 `codepilot auto-pr` 命令，用来消费第十二步生成的 `pr_assist_manifest.json`，产出受控的 Auto PR 计划、manifest 和 GitHub Action 模板。
这个命令默认是 **dry-run**，不会直接 push，也不会直接创建 PR。

```bash
# 1. 先完成 issue workflow
codepilot issue --issue-file issue.md --repo . --run-id issue-test --overwrite

# 2. 再生成 pr-assist 产物
codepilot pr-assist --run-id issue-test --prepare-branch --commit --overwrite

# 3. 默认 dry-run，只生成计划与 manifest
codepilot auto-pr --run-id issue-test --repo-slug owner/repo --overwrite
```

运行完成后会在 `runs/<run_id>/` 下新增：

- `auto_pr_plan.md`
- `auto_pr_manifest.json`
- `controlled_auto_pr_workflow.yml`

默认 dry-run 行为：

- 不 push
- 不 create PR
- 不 comment
- 只生成计划文件和受控 workflow 模板

如果要真正执行远端副作用，必须显式打开执行开关：

```bash
codepilot auto-pr \
  --run-id issue-test \
  --repo-slug owner/repo \
  --execute \
  --allow-push \
  --allow-create-pr \
  --token-env GITHUB_TOKEN \
  --overwrite
```

执行模式的关键规则：

- 只允许 push 到 `codepilot/<safe-run-id>`
- 不使用 `--force`
- 不使用 `--mirror`
- 不使用 `--all`
- 只有 push 成功且 remote ref 校验通过后才会创建 PR
- `--allow-comment` 默认关闭，只有显式打开才会尝试 issue comment
- `--no-dry-run` 单独传入无效，必须搭配 `--execute`

如果只想生成 workflow 模板给 GitHub Actions 使用，可以直接查看：

- `runs/<run_id>/controlled_auto_pr_workflow.yml`

这个模板默认：

- 顶层 `permissions: {}`
- plan job 只读
- execute job 只有在 `dry_run=false` 且 `create_pr=true` 时才执行
- 不自动写入 `.github/workflows/`

### CI Feedback / PR Review Loop 使用说明

第十四步新增了 `codepilot pr-feedback` 命令，用来消费第十三步生成的 `auto_pr_manifest.json`，读取 PR 的 checks、CI 日志和 review comments，并生成 follow-up 任务、更新计划和反馈报告。
默认也是 **dry-run**，只生成本地产物，不会运行 follow-up agent，也不会更新 PR 分支。

```bash
# 1. 先完成 issue workflow
codepilot issue --issue-file issue.md --repo . --run-id issue-test --overwrite

# 2. 再生成 pr-assist 产物
codepilot pr-assist --run-id issue-test --prepare-branch --commit --overwrite

# 3. 生成 auto-pr 产物
codepilot auto-pr --run-id issue-test --repo-slug owner/repo --overwrite

# 4. 读取 PR feedback，默认 dry-run
codepilot pr-feedback --run-id issue-test --overwrite
```

运行完成后会在 `runs/<run_id>/` 下新增：

- `ci_status.json`
- `review_feedback.json`
- `ci_feedback_report.md`
- `followup_task.md`
- `pr_update_plan.md`
- `ci_feedback_manifest.json`
- `pr_feedback_workflow.yml`

如果要真正执行 follow-up agent 和 PR 分支更新，需要显式打开执行开关：

```bash
codepilot pr-feedback \
  --run-id issue-test \
  --execute \
  --allow-run-agent \
  --allow-push-update \
  --allow-comment \
  --overwrite
```

执行模式的关键规则：

- 只允许在 `codepilot/` 开头的受控 PR 分支上工作
- `--allow-push-update` 必须搭配 `--allow-run-agent`
- `--no-dry-run` 单独传入无效，必须搭配 `--execute`
- 默认不会写完整 CI 日志到 `followup_task.md` 或 `ci_feedback_report.md`
- 默认不会把 token、secret 或 Authorization 原文写入产物

如果只想查看 GitHub Actions 模板，可以直接看：

- `runs/<run_id>/pr_feedback_workflow.yml`

这个模板默认：

- 顶层 `permissions: {}`
- `feedback-plan` job 只读
- `execute-update` job 只有在 `follow_up=true` 且 `update_pr=true` 时才执行
- 不自动写入 `.github/workflows/`

### Manual PR Assist 使用说明

第十二步新增了 `codepilot pr-assist` 命令。它只读取第十一步 `codepilot issue` 已经生成的 artifacts，再补出人工提 PR 所需材料：

- `pr_body.md`
- `manual_pr_commands.md`
- `review_checklist.md`
- `github_action_template.yml`
- `pr_assist_manifest.json`

这个命令**不会**重新运行 agent，**不会**重新生成 Evidence Report，默认也**不会**创建 branch、commit、push、PR 或调用 GitHub API。

```bash
# 通过 run_dir 直接生成 PR assist 产物
codepilot pr-assist --run-dir runs/<run_id> --overwrite

# 通过 run_id + runs_dir 定位
codepilot pr-assist --run-id <run_id> --runs-dir runs --overwrite

# 允许在文档里带上注释形式的 gh pr create 示例
codepilot pr-assist --run-dir runs/<run_id> --include-gh-pr-command --overwrite

# 可选：仅准备本地 branch
codepilot pr-assist --run-dir runs/<run_id> --prepare-branch --branch-prefix codepilot --overwrite

# 可选：在 branch 基础上继续准备本地 commit
codepilot pr-assist --run-dir runs/<run_id> --prepare-branch --commit --overwrite
```

命令说明：

- `--strict-safety`：默认开启。若第十一步 safety gate 失败，仍会生成 review-only artifacts，但会阻止 branch / commit 准备。
- `--redact-absolute-paths`：默认开启。生成的命令文档会把仓库绝对路径写成 `<repo>`。
- `--include-gh-pr-command`：只会在 `manual_pr_commands.md` 中生成注释形式的 `gh pr create` 示例，不会真的执行。
- `--prepare-branch`：只创建本地 branch，不 push，不设置 upstream。
- `--commit`：只提交 patch metadata 中声明的 `changed_files`，不会提交 `runs/<run_id>/`、`.env`、`.github/workflows`、`.codepilot` 等受保护路径。
- `--no-github-action-template`：不生成 `github_action_template.yml`。

生成结果中的几个关键文件：

- `pr_body.md`：面向 PR 描述，汇总 issue、summary、changed files、tests、safety 和 evidence。
- `manual_pr_commands.md`：给人工执行的命令清单，默认不包含 `git push`，也不包含可直接执行的 `gh pr create`。
- `review_checklist.md`：人工复核清单，帮助确认 patch scope、tests、safety、worktree cleanup 和 PR body 内容。
- `github_action_template.yml`：只写到 `runs/<run_id>/` 里，作为后续 GitHub Automation 准备模板，不会写入 `.github/workflows/`。

### Issue Workflow Hardening 使用说明

第十一步为 `codepilot issue` 增加了仓库安全检查、隔离 worktree、产物清单和恢复说明。
这些能力只围绕 issue workflow 生效，不会自动执行 commit、push、创建 PR、stash 或 reset。

```bash
# 干净仓库，按默认 fail 策略运行
codepilot issue --issue-file issue.md --repo . --approve

# 仓库有未提交改动时，仅记录 warning 并继续
codepilot issue --issue-file issue.md --repo . --dirty-policy warn --approve

# 在隔离 worktree 中运行，避免直接改动原仓库
codepilot issue --issue-file issue.md --repo . --worktree --approve

# 运行结束后自动尝试清理 worktree
codepilot issue --issue-file issue.md --repo . --worktree --cleanup-worktree --approve

# 输出路径脱敏，适合后续分享 artifact
codepilot issue --issue-file issue.md --repo . --redact-absolute-paths --approve
```

新增参数说明：

| 参数 | 说明 |
|------|------|
| `--dirty-policy fail|warn|allow` | 控制原仓库存在未提交改动时是否拒绝、警告继续或直接允许 |
| `--worktree / --no-worktree` | 是否在隔离 worktree 中执行 agent |
| `--worktree-base-dir` | 指定 worktree 根目录，必须位于原仓库之外 |
| `--cleanup-worktree / --keep-worktree` | 运行后是否尝试移除 worktree |
| `--manifest / --no-manifest` | 是否写出 `artifact_manifest.json` |
| `--restore-plan / --no-restore-plan` | 是否写出 `restore_plan.md` |
| `--require-clean-source-for-worktree` | worktree 模式下是否要求原仓库必须干净 |
| `--worktree-branch-prefix` | worktree 分支前缀，默认 `codepilot` |
| `--redact-absolute-paths` | 在 manifest 中把绝对路径替换为 `[REDACTED_PATH]` |

新增产物说明：

| 文件 | 说明 |
|------|------|
| `artifact_manifest.json` | 记录 run 状态、repo safety 结果、patch metadata 和所有产物索引 |
| `restore_plan.md` | 提供人工恢复说明，不会建议 `git reset --hard` 或 `git clean -fd` |
| `changes.patch` | 仍然导出当前 effective repo 的二进制安全 diff |
| `pr_summary.md` | 新增 Repo Safety、Patch Metadata、Manifest、Restore plan 信息 |

## CodePilot Lite 第十步 - GitHub Issue Workflow 使用说明

第十步新增了围绕 issue 输入的完整工作流入口：`codepilot issue`。  
这个命令严格按固定链路执行：

```text
issue.md / GitHub issue URL
→ issue.json
→ MinimalAgentLoop
→ trace.jsonl
→ report.md / report.json
→ changes.patch
→ pr_summary.md
```

### 本地 issue 文件运行

```bash
codepilot issue \
  --issue-file examples/issues/add_bug.md \
  --repo /path/to/local/repo \
  --fake-actions tests/codepilot/fixtures/agent_actions_success.jsonl \
  --approve \
  --runs-dir runs \
  --run-id issue-demo \
  --overwrite
```

### GitHub issue URL 运行

默认会从环境变量 `GITHUB_TOKEN` 读取 token；如果不需要认证，也可以不设置。

```bash
export GITHUB_TOKEN=ghp_xxx

codepilot issue \
  https://github.com/<owner>/<repo>/issues/<number> \
  --repo /path/to/local/repo \
  --runs-dir runs \
  --run-id issue-demo \
  --overwrite
```

如果 token 放在别的环境变量中，可以用 `--github-token-env <ENV_NAME>` 指定变量名。

### 主要参数

- `--issue-file`：本地 Markdown issue 文件，与位置参数里的 GitHub issue URL 二选一。
- `--repo`：要修复的本地仓库路径。
- `--policy-mode`：权限模式，支持 `read_only`、`build`、`danger`。
- `--approve`：批准 `ask` 类工具执行。
- `--fake-actions`：传入 JSONL 假响应，便于离线演示或测试。
- `--max-steps`：覆盖默认 agent 最大步数。
- `--report / --no-report`：是否生成 `report.md`。
- `--json-report / --no-json-report`：是否生成 `report.json`。
- `--runs-dir`、`--run-id`：指定产物输出目录。
- `--overwrite`：覆盖已有产物文件。

### 产物说明

执行成功后会在 `runs/<run_id>/` 下生成以下文件：

- `issue.json`：结构化 issue 输入。
- `trace.jsonl`：完整运行轨迹。
- `report.md`：Evidence Report Markdown。
- `report.json`：Evidence Report JSON。
- `changes.patch`：`git diff --binary` 导出的补丁。
- `pr_summary.md`：可直接复用的 PR 摘要。

### 行为边界

- 不会自动执行 `git commit`。
- 不会自动执行 `git push`。
- 不会自动创建 Pull Request。
- 不会把 GitHub token 写入 `issue.json`、trace、report 或 PR 摘要。

`report` 在生成 `RunReport.run_id` 时，严格按下面顺序取值：

1. `run_start` 事件中的 `run_id`
2. `trace_path.parent.name`，但仅限 `trace.jsonl` 位于类似 `runs/run-abc/trace.jsonl` 或 `runs/demo-xyz/trace.jsonl` 这类真实 run 目录下
3. trace 中第一个非空的事件顶层 `run_id`
4. trace 中第一个非空的 `metadata.run_id`
5. `unknown-run`

这意味着：

- 如果 trace 文件位于标准 `runs/<run_id>/trace.jsonl` 目录结构中，而 `run_start` 缺失，生成的 report 会优先使用目录名作为 `run_id`
- 如果 trace 文件只是放在普通临时目录里，例如 `/tmp/trace.jsonl`，则不会错误使用临时目录名，而是回退到事件里的 `run_id`

## CodePilot Lite 第七步 — Verification Tools v1

第七步在现有结构化工具层上新增了 3 个 verification 工具：

| 工具 | 风险等级 | 默认权限 | 说明 |
|------|----------|----------|------|
| `run_tests` | local_execution | ask | 在仓库根目录执行显式给出的测试命令，并返回 pytest 摘要结果 |
| `git_status` | read_only | allow | 使用 `git status --short` 查看仓库变更 |
| `git_diff` | read_only | allow | 查看 diff 摘要，或对指定安全路径查看内容 diff |

这一阶段严格按计划实现，边界如下：

- 不自动选择测试命令
- 不自动 commit / push
- 不自动 rollback / restore
- 不新增 LLM、agent loop、prompt、Evidence Report、MCP

### 使用示例

```bash
# 运行测试命令（直接 tool 调试默认会被拦截，需要显式加 --unsafe-direct）
codepilot tool run_tests '{"repo":".","command":"python -m pytest tests/codepilot -q"}' --unsafe-direct

# 通过 route 走策略检查执行安全测试命令
codepilot route '{"tool_name":"run_tests","arguments":{"repo":".","command":"python -m pytest tests/codepilot -q"}}' --approve

# 查看 git 状态
codepilot tool git_status '{"repo":"."}'

# 查看 diff 摘要
codepilot tool git_diff '{"repo":"."}'

# 查看指定文件的内容 diff
codepilot tool git_diff '{"repo":".","path":"src/codepilot/tools/base.py","include_content":true}'
```

### 结果说明

- `run_tests` 返回统一的 `ToolResult`，`metadata` 中会包含 `status`、`returncode`、`failed_tests`、`timed_out` 等字段。
- `git_status` 会在 `metadata` 中返回 `changed_files`、`staged_files`、`unstaged_files`、`untracked_files`、`deleted_files`、`renamed_files`。
- `git_diff` 的摘要模式只返回文件级变化；内容模式要求必须传入 `path`，并会对疑似 secret 内容做 `[REDACTED]` 脱敏。
- `run_tests` 执行 Python 测试时会设置 `PYTHONDONTWRITEBYTECODE=1`，默认不在仓库里生成 `__pycache__`，避免污染后续 `git_status` 和 agent loop 的 `changed_files` 输出。

```bash
# 指定 trace 根目录和 run_id
codepilot tool list_files '{"repo":".","path":".","max_depth":2}' --trace --runs-dir runs --run-id run-test
```

`--trace` 开启后，命令标准输出仍然会先打印 `ToolResult` JSON，随后再打印 `Trace written to: <path>`。
`--run-id` 可以复用同一个运行目录，`TraceLogger` 会在已有 `trace.jsonl` 的最大 `step` 后继续追加。
`runs/` 目录默认会被忽略，避免把运行时 trace 文件提交到仓库。

### ToolRouter 使用说明

第四步新增了 `ToolRouter`，它会接收一个结构化 `ToolAction`，先写入 `run_start` / `run_end` 事件，再按顺序把动作路由到 traced tool call。

```bash
# 路由一个结构化动作
codepilot route '{"tool_name":"list_files","arguments":{"repo":".","path":".","max_depth":2}}'

# 指定 trace 目录、复用 run_id，并控制 output 预览长度
codepilot route '{"tool_name":"read_file","arguments":{"repo":".","path":"src/codepilot/tools/base.py","start_line":1,"end_line":40},"reason":"检查基础类型"}' \
  --runs-dir runs \
  --run-id run-test \
  --output-preview-chars 500
```

`route` 命令接收一个 JSON 字符串，必须包含 `tool_name`，可选包含 `arguments`、`reason` 和 `metadata`。
执行成功后会输出 `ToolRouteResult` 的格式化 JSON，并在最后打印 `Trace written to: <path>`。
如果工具执行失败，CLI 仍然返回 0，失败信息会保留在 `result.error` 和 `success=false` 中。

### 第五步 PolicyChecker 使用说明

`route` 命令在第五步默认开启 `PolicyChecker`，它会在真正调用工具前先写入一条 `policy_decision` trace 事件。

```bash
# 默认启用 policy
codepilot route '{"tool_name":"list_files","arguments":{"repo":".","path":"src/codepilot","max_depth":2}}'

# 允许需要确认的动作继续执行
codepilot route '{"tool_name":"run_shell","arguments":{"repo":".","command":"echo hi"}}' --approve

# 关闭 policy，保持第四步的原始路由行为
codepilot route '{"tool_name":"run_shell","arguments":{"repo":".","command":"echo hi"}}' --no-policy

# 切换 policy 模式
codepilot route '{"tool_name":"run_shell","arguments":{"repo":".","command":"python -m pytest tests/codepilot -q"}}' --policy-mode build
codepilot route '{"tool_name":"run_shell","arguments":{"repo":".","command":"python -m pytest tests/codepilot -q"}}' --policy-mode read_only
```

默认策略规则如下：

- `list_files`、`read_file`、`search_code` 默认允许
- `run_shell` 默认需要审批，但 `pytest`、`python -m pytest`、`ruff`、`mypy`、`npm test`、`npm run test`、`npm run lint` 这类安全前缀在 `build` 和 `danger` 模式下会直接放行
- `.env`、`secrets`、`.ssh` 及其子路径会被拒绝
- `rm -rf`、`git push`、`git reset --hard`、`npm publish`、`curl `、`wget `、`ssh `、`scp `、`chmod 777` 会被拒绝

`route` 输出的 `ToolRouteResult.metadata` 会包含 `policy_decision`、`policy_reason`、`policy_rule`、`policy_mode`、`requires_approval`、`approved` 和 `executed`，便于直接查看这次路由为什么被允许、拒绝或要求审批。

### 第六步 Edit Tools v1 使用说明

第六步新增了两个编辑工具：`replace_range` 和 `apply_patch`。
这一步只负责“安全地改文件”，不包含测试运行、git diff、commit、push、LLM loop 或其它兜底逻辑。

```bash
# 直接调用只读工具仍然允许
codepilot tool read_file '{"repo":".","path":"src/codepilot/tools/base.py","start_line":1,"end_line":40}'

# 直接调用编辑工具默认会被拦截
codepilot tool replace_range '{"repo":".","path":"src/demo.py","start_line":2,"end_line":2,"replacement":"    return \"new\"\n"}'

# 仅用于本地调试时显式放行副作用直调
codepilot tool replace_range '{"repo":".","path":"src/demo.py","start_line":2,"end_line":2,"replacement":"    return \"new\"\n"}' --unsafe-direct

# 通过 route 走 PolicyChecker，编辑工具会先进入 ask 再决定是否执行
codepilot route '{"tool_name":"apply_patch","arguments":{"repo":".","patch":"diff --git a/src/demo.py b/src/demo.py\n--- a/src/demo.py\n+++ b/src/demo.py\n@@ -1 +1 @@\n-old\n+new\n"}}' --approve
```

`replace_range` 会返回一份统一 diff 预览，`apply_patch` 会先执行 `git apply --check`，通过后再决定是否真实应用。
两者默认权限都是 `ask`，并且会被 `PolicyChecker` 按路径规则检查；像 `.env`、`secrets`、`.ssh` 这类敏感路径会直接拒绝。
如果你只是想在命令行里调试编辑工具本身，可以使用 `--unsafe-direct`，否则有副作用的工具不会走直调入口。

## CodePilot Lite 第八步 — Minimal LLM Loop 使用说明

第八步在现有结构化工具层之上新增了一个最小的 LLM 闭环。模型每轮只能输出一个 JSON `AgentAction`，然后统一经由 `ToolRouter -> PolicyChecker -> call_tool_traced(...)` 执行工具，不会直接绕过路由器调用 `read_file`、`replace_range`、`run_tests` 等工具函数。

### AgentAction 格式

模型每轮只能返回一个 JSON object，类型只允许 `tool_call` 或 `finish`。

```json
{"type":"tool_call","tool_name":"read_file","arguments":{"path":"src/calc.py","start_line":1,"end_line":20}}
```

```json
{"type":"finish","status":"success","summary":"Fixed the bug and verified tests passed."}
```

约束如下：

- 不允许输出 Markdown fenced JSON
- 不允许一次返回多个 action
- `repo` 参数通常不需要模型填写，loop 会自动注入当前仓库
- 修改前应先 `read_file` 或 `search_code`
- 修改后应执行 `run_tests`
- `finish` 前应执行 `git_status` 或 `git_diff`

如果真实模型偶尔输出字段别名，当前第八步会在解析阶段自动归一化这些常见写法：

- `action` -> `type` 或 `tool_name`
- `tool` / `name` / `function_name` / `function` -> `tool_name`
- `parameters` / `input` / `args` -> `arguments`
- `type=final` -> `type=finish`

归一化只负责把常见字段别名整理成标准 `AgentAction`，不会跳过 `ToolRouter -> PolicyChecker -> call_tool_traced(...)` 这条执行链。你可以用下面的 fake actions 文件直接验证这条兼容路径：

```bash
PYTHONPATH=src python -m codepilot.cli agent-run \
  "Fix the failing add test" \
  --repo /tmp/codepilot-agent-demo \
  --fake-actions tests/codepilot/fixtures/agent_actions_aliases.jsonl \
  --approve \
  --policy-mode build \
  --run-id demo-agent-alias-actions
```

运行后，`runs/demo-agent-alias-actions/trace.jsonl` 里的 `agent_action` 事件会带上 `normalization_applied`、`normalized_fields`、`raw_action_preview` 和 `normalized_action_preview`，方便排查真实 LLM 的字段漂移。

### 使用 fake actions 运行最小闭环

可以用 JSONL 文件驱动 `FakeLLMClient`，逐步验证 loop 行为：

```bash
PYTHONPATH=src python -m codepilot.cli agent-run \
  "Fix the failing add test" \
  --repo /tmp/codepilot-agent-demo \
  --fake-actions tests/codepilot/fixtures/agent_actions_success.jsonl \
  --approve \
  --policy-mode build \
  --run-id demo-agent-loop
```

CLI 输出会包含：

- `Status: <status>`
- `Success: <true/false>`
- `Steps: <n>`
- `Changed files:`
- `Tests: <passed/failed/unknown>`
- `Policy violations: <n>`
- `Trace: runs/<run_id>/trace.jsonl`

在默认 FakeLLM 演示路径下，如果测试命令是 `python -m pytest ...`，`Changed files` 应只保留真实源码改动，例如 `src/calc.py`，不会再因为测试执行额外带出 `src/__pycache__/` 或 `tests/__pycache__/`。

### 使用 mini-SWE-agent 现有模型配置

如果不传 `--fake-actions`，`agent-run` 会复用 mini-SWE-agent 现有模型配置与模型构造逻辑，不新增第二套 provider、api key 或 base url 配置：

```bash
PYTHONPATH=src python -m codepilot.cli agent-run \
  "Inspect the repository and propose a fix" \
  --repo . \
  --model-config mini \
  --model anthropic/claude-sonnet-4-5
```

`agent-run` 支持的参数只有：

- `--repo`
- `--max-steps`
- `--policy-mode`
- `--approve`
- `--fake-actions`
- `--model`
- `--model-config`
- `--runs-dir`
- `--run-id`

不会新增计划外参数，例如 `--llm-api-key`、`--llm-base-url`。

## Attribution

If you found this work helpful, please consider citing the [SWE-agent paper](https://arxiv.org/abs/2405.15793) in your work:

```bibtex
@inproceedings{yang2024sweagent,
  title={{SWE}-agent: Agent-Computer Interfaces Enable Automated Software Engineering},
  author={John Yang and Carlos E Jimenez and Alexander Wettig and Kilian Lieret and Shunyu Yao and Karthik R Narasimhan and Ofir Press},
  booktitle={The Thirty-eighth Annual Conference on Neural Information Processing Systems},
  year={2024},
  url={https://arxiv.org/abs/2405.15793}
}
```

Our other projects:

<div align="center">
  <a href="https://github.com/SWE-agent/SWE-agent"><img src="https://raw.githubusercontent.com/SWE-agent/swe-agent-media/refs/heads/main/media/logos_banners/sweagent_logo_text_below.svg" alt="SWE-agent" height="120px"></a>
   &nbsp;&nbsp;
  <a href="https://github.com/SWE-agent/SWE-ReX"><img src="https://raw.githubusercontent.com/SWE-agent/swe-agent-media/refs/heads/main/media/logos_banners/swerex_logo_text_below.svg" alt="SWE-ReX" height="120px"></a>
   &nbsp;&nbsp;
  <a href="https://github.com/SWE-bench/SWE-bench"><img src="https://raw.githubusercontent.com/SWE-agent/swe-agent-media/refs/heads/main/media/logos_banners/swebench_logo_text_below.svg" alt="SWE-bench" height="120px"></a>
  &nbsp;&nbsp;
  <a href="https://github.com/SWE-bench/SWE-smith"><img src="https://raw.githubusercontent.com/SWE-agent/swe-agent-media/refs/heads/main/media/logos_banners/swesmith_logo_text_below.svg" alt="SWE-smith" height="120px"></a>
  &nbsp;&nbsp;
  <a href="https://github.com/codeclash-ai/codeclash"><img src="https://raw.githubusercontent.com/SWE-agent/swe-agent-media/refs/heads/main/media/logos_banners/codeclash_logo_text_below.svg" alt="CodeClash" height="120px"></a>
  &nbsp;&nbsp;
  <a href="https://github.com/SWE-bench/sb-cli"><img src="https://raw.githubusercontent.com/SWE-agent/swe-agent-media/refs/heads/main/media/logos_banners/sbcli_logo_text_below.svg" alt="sb-cli" height="120px"></a>
</div>
