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
| `list_files` | read_only | allow | 列出仓库目录树，支持 offset 分页 |
| `read_file` | read_only | allow | 读取文件片段并保留行号 |
| `search_code` | read_only | allow | 在源码中搜索关键词 |
| `run_shell` | shell_execution | ask | 在仓库根目录执行 shell 命令（高风险 fallback） |

每个工具都返回统一的 `ToolResult`，包含 `success`、`output`、`output_summary`、`error` 和 `metadata` 字段。
`metadata` 中始终携带 `risk`、`duration_ms`、工具特有参数以及 `truncated` 截断标记。

### 使用示例

```bash
# 查看所有工具
codepilot tools

# 列表目录第一页
codepilot tool list_files '{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":0}'

# 列表目录下一页
codepilot tool list_files '{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":200}'

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
`list_files` 严格按 `max_depth` 控制返回层级深度，不会越界。`max_entries` 是单页条目上限，默认 200；当结果 `has_more=true` 时，使用 `next_offset` 继续读取下一页，同一轮翻页必须保持 `path`、`max_depth`、`include_hidden`、`max_entries` 不变。

### CodePilot Session SQLite 核心

这一版新增了独立的 `codepilot.session` 包，专门负责 Session 的 SQLite 持久化。它只做底层数据读写，不会自动接入现有 TUI 或 LLM 流程。

最小用法如下：

```python
from pathlib import Path

from codepilot.session import SessionDatabase, SessionStore, resolve_session_paths

paths = resolve_session_paths(Path("/tmp/codepilot-data"))
database = SessionDatabase(paths.database_path)
database.initialize()

store = SessionStore(database, paths)
session = store.create_session(
    project_path=Path("/tmp/repo"),
    provider="openai",
    current_model="gpt-4.1",
    permission_mode="manual",
)
turn = store.create_turn(
    session_id=session.session_id,
    title="Turn 1",
    provider_snapshot="openai",
    model_snapshot="gpt-4.1",
    permission_mode_snapshot="manual",
    branch_snapshot="main",
)
```

### CodePilot Lite Session TUI 使用说明

启动 TUI 后，先通过 Session Picker 选择已有 Session，或者按 `n` 新建一个 Session。
输入框始终可用，普通文本会作为新任务提交，带 `/` 前缀的内容会被当成命令处理。

常用命令如下：

- `/sessions` 打开 Session Picker
- `/switch <session-id>` 切换到指定 Session
- `/new` 新建一个 Session
- `/archive` 归档当前 Session
- `/unarchive <session-id>` 取消归档指定 Session
- `/compact` 先执行上下文压缩，再继续当前 Session
- `/export-session [path]` 导出当前 Session 到指定目录
- `/move <path>` 把后续新建 Session 的项目目录切换到指定路径

导出目录只影响导出结果，不会自动切换当前 Project、Session 或 Transcript。
Session 的历史内容、权限请求和工具结果都以 SQLite 为唯一事实来源，切换 Session 后会自动从数据库重新挂载。

说明：

* `resolve_session_paths(...)` 默认会指向用户级 `codepilot` 数据目录，也可以在测试里显式传入 `tmp_path`。
* `SessionDatabase.initialize()` 会创建 SQLite schema，重复调用是安全的。
* `SessionStore` 负责创建和读取 `projects`、`sessions`、`turns`、`messages`、`tool_calls`、`session_events` 等核心记录。
* 这一阶段不会创建旧的 `session.json`、`messages.jsonl` 或 `runs.jsonl`。

当前这版 Session 的推荐入口是先创建数据库和 Store，再按需接入 `SessionService`、`SessionRuntime` 和 `SessionPermissionBroker`。`codepilot.session` 包已经改成轻量导出，导入它不会再强制加载 runtime，适合在 CLI、TUI 和测试里直接使用。

### Session 生命周期与多轮执行（Step3–Step5）

Step3–Step5 在 SQLite 核心之上提供 `SessionService`、`ContextAssembler`、`SessionRuntime` 和 `SessionTraceRecorder`。Session 的历史由数据库重新组装，`MinimalAgentLoop.run_turn()` 接收已经组装好的消息；旧的 `run(task, repo)` 仍用于非 Session CLI。

```python
from pathlib import Path

from codepilot.session import (
    BranchConfirmationRequired,
    SessionDatabase,
    SessionRuntime,
    SessionService,
    TurnSubmission,
    resolve_session_paths,
)

paths = resolve_session_paths(Path("/tmp/codepilot-data"))
database = SessionDatabase(paths.database_path)
database.initialize()
service = SessionService(database, paths)
session = service.create_session(Path("/tmp/repo"), "openai", "gpt-4.1", "manual")

# SessionRuntime 需要应用层提供 LLM 客户端和
# `router_factory(trace_recorder)`，以便工具路由与 SessionTraceRecorder 共用同一事实流。
# runtime = SessionRuntime(database, llm, router_factory)

# 如果返回 BranchConfirmationRequired，界面应保留原始文本并询问用户；确认时把
# new_branch 作为 confirmed_branch 再次提交。Runtime 会重新读取真实 Git 分支，
# 不会把第一次检查结果当作执行依据。
submission = runtime.submit_user_message(session.session_id, "请检查项目状态")
if isinstance(submission, BranchConfirmationRequired):
    submission = runtime.submit_user_message(
        session.session_id,
        "请检查项目状态",
        confirmed_branch=submission.new_branch,
    )
if isinstance(submission, TurnSubmission):
    result = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)
```

`SessionService.open_session()` 在项目路径不存在时仍允许读取历史，但运行新 Turn 会拒绝。检测到 Git 分支变化时只返回确认信息，不会提前创建 Turn；用户确认后，`branch_changed`、Session 当前分支、Turn、User Message、Attempt 1、首条消息标题以及 `turn_created` / `user_message_created` 事件在同一个 SQLite 事务内提交。确认期间实际分支再次变化时会返回新的确认请求，取消确认不会写入上述任何记录。Session 模式的 Trace 写入 `session_events`，不会创建 `trace.jsonl` 或 run 目录。

#### Attempt、工具生命周期与恢复（P0 3.4–3.8）

Session SQLite Schema 当前为 v4。已有 v1/v2/v3 数据库会在 `SessionDatabase.initialize()` 时原地升级并补齐下面这些字段，原 Session、Turn、Message、Attempt、ToolCall、Permission 和 Summary 记录会保留：

```text
tool_calls.side_effect
tool_calls.idempotency
tool_calls.recovery_strategy
tool_calls.recovery_token_json
run_attempts.interruption_reason
run_attempts.worker_id
run_attempts.lease_expires_at
turns.user_message_id
turns.started_at
turns.completed_at
turns.error_code
context_summaries.source_start_sequence
context_summaries.source_end_sequence
context_summaries.summary_message_id
context_summaries.model
context_summaries.status
```

`SessionRuntime.submit_user_message()` 原子创建的 Attempt 由 `run_turn(turn_id, attempt_id)` 精确执行。模型调用前，Turn 和 Attempt 在同一事务中进入 `running`；结束状态映射如下：

- `success` / `message_complete`：Attempt 和 Turn 均为 `completed`；
- `cancelled`：Attempt 和 Turn 均为 `cancelled`；
- `llm_error`、`llm_exhausted`、`max_steps_exceeded`、`task_incomplete` 等非成功结果：均为 `failed`；
- Runner、Router 或上下文准备阶段的未捕获异常：均为 `interrupted`，错误写入 `run_attempts.interruption_reason`。

Attempt 使用条件状态更新：只有 `created + queued` 可以进入执行，只有当前 `running` Attempt 可以写终态，已结束 Attempt 不能重复运行。每个 Attempt 使用唯一 Worker ID，并在执行期间定时续租；终态写入还会校验 Worker 所有权。Picker 打开同一 Session 时不会把 lease 仍有效的 Worker 误判成崩溃恢复。已经创建但尚未来得及启动的恢复 Attempt 会在下次打开 Session 时重新调度；多个恢复 Turn 按顺序逐个认领，不并行恢复。

工具业务表由 `SQLiteToolLifecycleObserver` 维护。每次 Router 调用都会创建新的 `tool_call_id`，后续 policy、权限、执行开始、结果和执行异常都只通过该 ID 更新，因此同一 Turn 中重复调用相同工具和相同参数也不会串行。Policy deny 和 Permission deny 会产生 `denied` ToolCall 与 ToolResult，并记录 `executed=false`。

写工具执行前会先保存恢复 Token：

- `replace_range` 保存目标规范化路径、执行前全文件 SHA-256、预期全文件 SHA-256、行范围和 replacement SHA-256；
- `apply_patch` 保存 patch SHA-256、仓库路径、可用时的 baseline HEAD 和执行前 forward check；
- `run_shell` 保存命令 SHA-256，并只对严格只读 allowlist 标记可自动重试。`git commit`、`git checkout`、`git reset`、`git clean`、`find -delete` 和任何管道、重定向或复合 Shell 命令都不会自动重试。

显式从 Picker 打开 active 且项目路径存在的 Session 时，`RecoveryService.recover_session()` 会：

1. 将硬中断遗留的 `in_progress` Assistant Message 改为 `interrupted`；
2. 对没有 ToolResult 的 uncertain ToolCall 使用持久化 Token 对账；
3. `COMPLETED` 写入唯一的 `recovered_completed` Result；
4. `NOT_EXECUTED` 写入唯一的 `recovered_not_executed` Result，并为原 Turn 创建新的 Attempt；
5. `PARTIALLY_COMPLETED` / `UNKNOWN` 把 Turn 标记为 `recovery_required`，显示 Recovery Modal；
6. 用户可选择 `Mark completed`、`Retry` 或 `Abort`。Retry 保留原 ToolCall 的 uncertain 历史，并明确记录用户接受重复副作用风险。

`COMPLETED`、`NOT_EXECUTED` 和用户 `Mark completed` 还会写入可回放的 system Message，使新的 Attempt 在模型上下文中明确看到恢复事实，避免重复已经确认完成的写操作。`Abort` 以 Turn 为粒度终结该 Turn 下全部 uncertain ToolCall、旧 Attempt 和 pending permission request，不会留下可再次排队的半终态。

项目路径缺失或 Session 已归档时只执行恢复检查，不自动执行恢复 Attempt。
遗留的 pending permission request 和 `approval_pending` ToolCall 也会阻断自动恢复并在 TUI 中显示等待状态；在第 3.9 的 Session Permission Broker 接入完成前，不会用自动重试绕过旧审批。

### 工具恢复、Session 权限与流式消息（Step6–Step8）

Step6 为每个内置工具声明 `idempotency` 和 `recovery_strategy`。工具执行前会通过 `ToolLifecycleObserver` 记录创建、执行开始和执行结束；`RecoveryService` 只会自动重试可确认未执行的低风险工具，无法确认的副作用会进入 `recovery_required`，不会静默重复执行。

Step7 的 `SessionPermissionBroker` 将权限 Request、Response 和 `approve_session` Grant 写入 SQLite。授权范围按工具收窄：编辑工具绑定 workspace，Shell 绑定规范化命令哈希，外部/MCP 工具绑定 server、tool 和参数哈希。Policy deny 不会被 Session Grant 覆盖。

在 TUI 的权限弹窗里，如果请求带有 `Session scope`，会额外显示 `Approve for session`。按 `S` 之后，本次 request、response、grant 和对应 Session Event 都会写入同一个 Session 数据库，后续相同 scope 的请求会直接复用这条授权。

Step8 为 LLM 增加可选 `stream()` 接口，旧的 `complete()` 不变。流式文本和 reasoning delta 会按顺序持久化；进程中断时 Assistant 保持 `interrupted`，恢复时由新的 Attempt 重新生成完整回答，不从最后字符直接续写。

### Context Compact、模型切换与 Session Picker（Step9–Step10）

`CompactionService` 使用有限的 `ModelContextProfile` 估算上下文，在达到阈值后生成结构化摘要并写入 `context_summaries`；原始消息不会删除，摘要失败会记录 `context_compaction_failed` 事件并阻断本次压缩。`SessionService.change_model()` 只允许同一 Provider 内切换，并且后续 Turn 继续保存真实模型快照。

TUI 启动方式：

```bash
codepilot tui /path/to/project
```

启动后不会自动创建空 Session，而是先显示跨项目 Session Picker：

- `Enter`：打开选中的已有 Session；
- `n`：为命令行指定的项目创建新 Session；
- `a`：切换是否显示归档 Session；
- `Esc`：关闭 Picker，输入框继续保持禁用。

TUI、Runner、Picker 和 Exporter 共用 `resolve_session_paths()` 返回的用户级 SQLite，项目目录只记录在 `projects.path`，不会再创建项目内 `.codepilot/sessions.sqlite`。显式传入 `session_database` 时，所有组件也会共用该数据库。打开项目路径已删除或已归档的 Session 时输入框禁用，只读状态不会被转成新的项目 Session。

提交任务后如果 Git 分支与 Session 记录不一致，TUI 会显示分支确认弹窗。选择 Continue 会携带完整原始输入重新提交，并再次检查当前实际分支；选择 Cancel 不创建 Turn、User Message、Attempt 或 Session Event。

### 手动 Session 导出与旧存储边界（Step11–Step12）

`SessionExporter` 只有在显式调用 `export(session_id, target_root)` 时才创建目录，输出固定 v2 结构：`manifest.json`、`session.json`、`turns.jsonl`、`messages.jsonl`、`events.jsonl`、`trace.jsonl`、`report.json` 和 `artifacts/`。导出使用 SQLite 事务快照、临时目录和原子重命名；Runtime 不读取导出目录。

Session Runtime 的事实来源始终是 SQLite；旧的文件型 Trace/Report 仍只服务于非 Session 的 `agent-run`、GitHub/PR/CI 等既有入口。Session 导出文件不能被当作恢复状态，也不会在正常 Session 运行时自动生成。

Step12 已将 TUI 的 Session 主类型切换为 SQLite `SessionRecord`，`TUIAgentRunner` 通过 `SessionRuntime` 创建 Turn/Attempt，不再构造 `TraceLogger` 或调用 `generate_report()`。旧的 Run 索引写入接口已移除，避免旧调用悄悄重新写入 JSON。

这次还把大输出统一切到 artifact 入口：消息分片和 Tool Result 现在只保留 `preview` 和 `artifact_id`，完整内容会落到 Session artifact store。想查看完整文本时，可以直接用 `ArtifactStore.read_text(artifact_id)` 读取；TUI 默认显示的是 preview。

### Chat-style TUI transcript helpers

这一阶段新增了聊天式 TUI 用到的 transcript 数据结构和格式化函数，方便把 trace 投影成可复制的纯文本消息流。

```python
from codepilot.tui_agent.layout import format_side_status, format_transcript_item, format_transcript_plain
from codepilot.tui_agent.models import TranscriptItem

item = TranscriptItem(
    id="msg-1",
    kind="user_message",
    timestamp="2024-01-01T00:00:00Z",
    title="You",
    body="请列出项目结构",
    copy_text="You: 请列出项目结构",
)

print(format_transcript_item(item))
print(format_transcript_plain((item,)))
```

可用的 transcript kind 包括 `user_message`、`assistant_plan`、`assistant_action`、`assistant_raw`、`tool_result`、`permission_request`、`permission_response`、`final_summary`、`command_output`、`system_status` 和 `error`。
如果需要显示右侧状态摘要，可以直接调用 `format_side_status(...)`，它默认不会展示完整的 `report.json`、`report.md` 或 `trace.jsonl` 路径。

### Phase 0-2 使用说明

这次重构后，权限和事件契约有两个最直接的变化：

- 权限请求和响应类型统一从 `codepilot.permissions` 导入。
- TUI 的权限弹窗和 reducer 只读取标准字段 `request_id`，不再依赖旧的 `permission_request_id`。
- `run_start` 和 `run_end` 仍会写入 trace 文件，但不会再作为重复的 TUI 生命周期事件显示。

常用入口没有变化，还是下面这几个命令：

```bash
# 启动交互式 TUI
codepilot tui <project>

# 路由一个结构化工具动作
codepilot route '{"tool_name":"list_files","arguments":{"repo":".","path":".","max_depth":2}}'

# 直接调用一个工具
codepilot tool list_files '{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":0}'
```

在 TUI 里，`/permissions` 仍然可以切换 `manual`、`read_only`、`accept_edits` 和 `unsafe_auto` 四种模式。
其中 `accept_edits` 仍然会自动批准本地写入，但不会影响其他需要确认的动作。

### Phase 3 运行结果快照使用说明

Phase 3 将运行结束状态集中到 `RunOutcomeSnapshot`，TUI Session 和 `run_finished` 事件现在使用同一份 Outcome 数据，外部 Session JSON 的字段名和层级没有变化。

如果需要在 Python 中读取新结构，优先访问 `result.outcome`：

```python
result = run_agent_task(task="fix add", repo=".")

print(result.outcome.status)
print(result.outcome.completion_kind)
print(result.outcome.changed_files)
print(result.outcome.last_test_status)
print(result.outcome.evidence.missing)
print(result.outcome.to_payload())
```

`outcome.to_payload()` 返回 TUI 结束事件使用的标准顶层字段，其中集合会在序列化边界转换为 list。为避免破坏已有调用，`result.changed_files`、`result.last_test_status` 和其他原有结果属性仍可只读访问；新代码应使用 `result.outcome`。

TUI 的命令入口和权限模式保持不变；Session 事实统一保存在用户级 SQLite，不再读取或创建项目内 `session.json` 作为主存储。

### Phase 4 Agent 结束与异常状态说明

Phase 4 没有改变 Agent 的调用命令、Prompt 或 Action JSON Schema，主要收敛了 Agent Loop 内部的结束和异常分类。继续使用原有命令即可：

```bash
codepilot agent-run "Fix the failing test" --repo . --approve
```

运行结果的状态含义保持不变：

- 自然文本回复或非代码交付的成功 finish 返回 `message_complete`。
- 代码交付证据完整时返回 `success`；缺少写入、测试或 diff 证据时继续下一轮。
- 模型明确返回 `failed` 或 `partial` 时分别结束为 `failed`、`partial`。
- 用户取消返回 `cancelled`，并且只记录一次有效的取消 Trace。
- Fake LLM 响应耗尽返回 `llm_exhausted`；只有 `llm.complete()` 本身抛出的其他异常才返回 `llm_error`。
- 达到最大步数返回 `max_steps_exceeded`，其 `completion_kind` 仍为 `runtime_failure`。

Trace、状态更新或其他 Agent 内部编程错误不会再被错误包装为 `llm_error`：TUI Runner 会按既有异常结束流程处理，CLI 则会直接显示异常。工具参数准备和 `router.route()` 的执行异常仍会作为 observation 返回给模型，使模型可以修正动作后继续运行。

### Phase 5 Post-PR 类型化状态使用说明

Phase 5 没有改变 `codepilot post-pr` 的命令参数、审批范围、安全分支检查或产物 JSON Schema。命令行仍按原有方式先生成审批请求，再使用 `--resume` 恢复同一轮：

```bash
# dry-run：只收集反馈并生成审批请求，不执行 Agent、Push 或 Comment
codepilot post-pr --run-dir runs/<run_id> --overwrite

# 审批后恢复同一 round_id；approval file 必须位于 run_dir/post_pr 内
codepilot post-pr \
  --run-dir runs/<run_id> \
  --execute \
  --resume \
  --approval-file runs/<run_id>/post_pr/approval_decision.json
```

`state.json` 和 `side_effects.json` 的磁盘结构仍是 `codepilot.post_pr.state.v1` 与 `codepilot.post_pr.side_effects.v1`。加载后，Python API 返回不可变的类型对象：

```python
from codepilot.post_pr.state_store import load_post_pr_state, load_side_effects

state = load_post_pr_state("runs/<run_id>/post_pr/state.json")
ledger = load_side_effects("runs/<run_id>/post_pr/side_effects.json", run_id="<run_id>")

if state is not None:
    print(state.status, state.terminal_reason, state.rounds)
print(ledger.effects)
```

状态中的 `rounds`、`blockers`、`warnings` 以及账本中的 `effects` 均为 tuple。更新同一轮应使用 `upsert_round(state, round_ref)`，它会替换相同 `round_id`，不会在 resume 时重复追加；写回仍使用 `write_post_pr_state(...)`。JSON 与 dataclass 的转换只由 State Store 负责，Controller 不再读取裸字典字段。

### Phase 6 Evidence 与 Trace 诊断说明

Phase 6 删除了未接入生产链路的任务关键词分类器。Agent 初始 `task_intent` 仍为 `general`，不会根据“修复”“只分析”等中英文关键词提前决定证据门禁；证据要求继续只依据真实工具写入尝试、实际写入、测试结果、`git_diff` 和结构化 finish 的交付类型动态升级。

`observed_changed_files` 仍会出现在 Agent 状态、运行结果、Trace、TUI 和 Report 中，用于展示 `git_status` 观测到的工作区脏文件，但它不再作为 `evaluate_evidence()` 的输入，也不会被当成本轮真实写入证据。调用 Evidence API 时不再传入该参数：

```python
from codepilot.agent.evidence import evaluate_evidence

decision = evaluate_evidence(
    task_requires_code_delivery=True,
    write_attempted=True,
    write_executed=True,
    written_files=["src/example.py"],
    claimed_changed_files=["src/example.py"],
    last_test_status="passed",
    diff_checked=True,
)
```

工具 action alias 仍通过工具注册表识别。若注册表查询本身发生编程错误，原始异常会直接暴露，不会再被改写为 `Unknown action alias`。

`TraceLogger` 仍然先写入 `trace.jsonl`，再调用可选的 `record_hook`。Hook 失败不会撤销或阻断已经完成的 Trace 写入；可以通过 `record_hook_error` 接收异常和对应事件，也可以检查 `last_record_hook_error`：

```python
from codepilot.trace import TraceLogger

def on_hook_error(error, event):
    print(event.event_type, error)

logger = TraceLogger(record_hook=publish_event, record_hook_error=on_hook_error)
```

TUI Runner 已将 Trace 事件桥接失败转换为 `source=trace_record_hook` 的 `error` 事件，方便在界面中诊断，同时不改变 Agent、Trace 和 Session 的主流程。如果错误回调自身也失败，TraceLogger 不会重试或改用其他发布通道，而是把该异常保存在 `last_record_hook_error_callback_error`，避免次要诊断链路中断 Agent。

TUI 里现在还支持两种复制方式：

```text
/copy
/copy last
/copy errors
/export-transcript
```

`/copy` 会打开纯文本复制视图，`Ctrl+A` 可全选，`Esc` 可关闭。`/export-transcript` 会把当前 transcript 导出到会话目录下的 `transcript.md`。

### TraceLogger 使用说明

第三步新增了结构化 trace 记录能力。默认 trace 文件会写到 `runs/<run_id>/trace.jsonl`，每一行都是一条 JSON 事件。

```bash
# 仅执行工具调用，不写 trace
codepilot tool list_files '{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":0}'

# 写入 trace，默认输出到 runs/<run_id>/trace.jsonl
codepilot tool list_files '{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":0}' --trace
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

### CodePilot Lite 第十八步使用说明

第十八步把“自然回复、读取事实、写入修改、测试验证”分成了更清晰的状态。
你现在可以这样理解输出：

- 普通问候、解释类问题，直接自然文本回复即可，结果通常是 `message_complete`。
- `message_complete` 的完整正文会来自 `agent_finish.output_summary`，所以长自然回复也会在 TUI 里完整显示一次，不会只保留前面的 trace 预览。
- 需要先确认仓库事实时，模型可以先调用读取工具，不会因此自动进入写入门禁。
- 只有真实执行过写入后，`Evidence Gate` 才会开始要求测试和 `git_diff` 证据。
- 如果写入被拒绝、写入没有真正生效、或者只是在 `finish` 里声明改了文件，报告和 TUI 都会显示缺失的证据项。
- `finish.changed_files` 只用于记录模型声明，不会替代真实写入证据；想要成功结束代码修改任务，仍然需要实际改文件、跑测试、再看 `git_diff`。
- `/permissions` 会明确说明 `manual` 模式下，读取工具可自动执行，写入和高风险动作仍需要确认。

如果你在 TUI 里查看结果，右侧状态栏和结果面板会显示：

- `Completion`
- `Evidence`
- `Tests`
- `Diff`
- `Missing evidence`

如果你在命令行生成报告，`report.md` 会新增 `Evidence Gate` 小节，展示本轮的完成类型、证据要求、写入轨迹和缺失项。

### CodePilot Lite 第十九步补充使用说明

这次补丁把 `finish.changed_files`、真实写入证据和 TUI 报告状态拆开了，使用时可以按下面理解：

- 如果模型只是声明 `changed_files`，但没有真实执行写文件动作，系统会把它当成“声明过修改”，不会把这些文件当作真实 `changed_files` 记入结果。
- 对于真正的代码交付任务，`finish` 需要配合真实写入、测试和 `git_diff` 证据；TUI 右侧状态栏会显示 `Tests: passed` 和 `Diff: checked`。
- 对于普通聊天或只读分析任务，`Tests` 和 `Diff` 会显示为 `not required`，报告里也会显示 `Status: not required` 和 `Diff was not required.`。
- 你可以通过查看 `report.md` 的 `Evidence Gate`、`Test Result` 和 `Diff Summary` 小节，确认这轮运行到底是代码交付还是普通回复。
- 如果想检查最终落盘结果，请执行 `/export-session`；正常 Session 运行不会生成项目内 `session.json`、`runs.jsonl`、trace 或 report 文件。

### MCP 工具接入使用说明

第十六步新增了 MCP 工具接入能力，默认使用 `fake` 传输，不依赖真实 MCP server。

```bash
# 列出 MCP 工具
codepilot mcp-tools --mcp-config examples/mcp/fake_filesystem_mcp.json

# 以受控方式调用 MCP 工具
codepilot mcp-call mcp.filesystem.read_file '{"path":"README.md"}' \
  --mcp-config examples/mcp/fake_filesystem_mcp.json

# 允许 ask 型工具执行 fake 调用
codepilot mcp-call mcp.filesystem.write_file '{"path":"demo.txt","content":"hello"}' \
  --mcp-config examples/mcp/fake_filesystem_mcp.json \
  --approve

# 在 agent-run 中注入 MCP 暴露工具
codepilot agent-run "Use MCP to read README and summarize it" \
  --repo . \
  --fake-actions examples/mcp/fake_actions_mcp_read_file.jsonl \
  --mcp-config examples/mcp/fake_filesystem_mcp.json \
  --approve
```

`examples/mcp/fake_filesystem_mcp.json` 提供了一个可直接运行的 fake 配置，适合本地测试和单元测试。
MCP 工具目录只会向 agent 暴露 `exposed_to_agent=true` 的工具，未暴露工具仍然可以通过 `mcp-call` 在策略允许时单独调用。
所有 MCP 调用都会写入 `trace.jsonl`，并记录 `mcp=true`、`server_name`、`mcp_tool_name`、`codepilot_tool_name` 和 `descriptor_hash` 等关键信息。

### CodePilot Lite 第十五步 - Post-PR Automation 使用说明

第十五步新增了 `codepilot post-pr` 命令，用来在 `auto-pr` 完成后继续做受控的 PR 后续处理。  
这个命令默认是 `dry-run`，只会读取 `runs/<run_id>/auto_pr_manifest.json` 和第十四步生成的反馈产物，不会自动运行 agent、不会 push、也不会发评论。
如果你希望审批请求里也包含 `post_comment`，那么计划阶段同样要传 `--approve-comment`，这样生成的 `approval_request.json` 才会把评论审批项写进去。

```bash
# 只做计划，生成 approval request / report / state
codepilot post-pr --run-dir runs/<run_id> --overwrite

# 使用 run-id 自动定位 runs/<run_id>
codepilot post-pr --run-id <run_id> --runs-dir runs --overwrite

# 计划阶段如果需要包含评论审批项，也要显式加上 --approve-comment
codepilot post-pr --run-dir runs/<run_id> --overwrite --approve-comment

# 显式进入 execute，但仍然需要审批文件或 CLI 授权参数
codepilot post-pr --run-dir runs/<run_id> --execute --resume --approval-file runs/<run_id>/post_pr/approval_decision.json

# 仅批准运行 follow-up agent
codepilot post-pr --run-dir runs/<run_id> --execute --approve-run-agent --resume

# 批准运行 agent 并允许继续推送分支更新
codepilot post-pr --run-dir runs/<run_id> --execute --approve-run-agent --approve-push-update --resume

# 如果还要批准评论发布，execute 阶段和 plan 阶段都要带上 --approve-comment
codepilot post-pr --run-dir runs/<run_id> --execute --approve-run-agent --approve-push-update --approve-comment --resume
```

默认会在 `runs/<run_id>/post_pr/` 下生成这些文件：

- `state.json`
- `side_effects.json`
- `approval_request.md`
- `approval_request.json`
- `approval_decision.json`
- `post_pr_automation_manifest.json`
- `post_pr_automation_report.md`
- `post_pr_automation_workflow.yml`
- `round-XXX/collect/...`
- `round-XXX/execute/...`

`approval_request.md` 只列出审批范围、相关哈希和待审产物，不包含完整日志、完整 diff、token 或环境变量。  
`approval_file` 必须放在 `runs/<run_id>/post_pr/` 目录内，不能指向目录外的文件。
如果要恢复执行，可以加 `--resume`，这样已经成功的 commit / push / comment 不会重复执行。
如果在 TUI 或事件流里看到 `type="error"` 的诊断事件，可以用 `source` 判断阶段：

- `runner_setup` / `agent_runtime` 表示这次执行没有拿到可信的 Agent 结果，最终 `run_finished.status` 会是 `failed`
- `report_generation` / `session_persistence` 表示 Agent 结果已经完成，只是报告或会话索引写入失败，`run_finished.status` 仍然保持真实 Agent 结果
- `failure_source` 只会出现在真正的运行失败里，不再把报告失败伪装成 `llm_error`

Known limitation: concurrent post-pr runs with the same `run_id` should be avoided; use one active post-pr process per `run_id`.

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
如果当前环境没有可用的 GitHub token，dry-run 会写出 `feedback_unavailable` 或 `api_degraded` 类型的产物，不会直接失败退出；`--execute` 在缺 token、stale head、pending checks 或未授权执行时会写出 `blocked` 产物。

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

其中 `pr_update_plan.md` 会直接反映 CLI 传入的模式和开关，例如：

- `Mode: dry-run` / `Dry run: yes`
- `Mode: execute` / `Dry run: no`
- `allow_run_agent`
- `allow_push_update`
- `allow_comment`

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

常用输入开关包括 `--repo-slug`、`--pull-number`、`--head-branch`、`--include-logs/--no-include-logs`、`--max-log-bytes`、`--max-feedback-items` 和 `--allow-comment`。

执行模式的关键规则：

- 只允许在 `codepilot/` 开头的受控 PR 分支上工作
- `--execute` 会在 stale head、pending checks、missing token 或 API degraded 时写出 `blocked` 产物
- `--allow-push-update` 必须搭配 `--allow-run-agent`
- `--no-dry-run` 单独传入无效，必须搭配 `--execute`
- 默认不会写完整 CI 日志到 `followup_task.md` 或 `ci_feedback_report.md`
- 默认不会把 token、secret 或 Authorization 原文写入产物
- `ci_feedback_manifest.json` 会保存 checks、logs、reviews、feedback_items 的安全摘要，以及本次输入的 `dry_run` / `execute` / `allow_*` 开关

如果要把这个命令放到 GitHub Actions 里执行，可以直接使用 `runs/<run_id>/pr_feedback_workflow.yml`，它会把 workflow inputs 传给 CLI，并且只上传白名单里的 artifact。

如果只想查看 GitHub Actions 模板，可以直接看：

- `runs/<run_id>/pr_feedback_workflow.yml`

这个模板默认：

- 顶层 `permissions: {}`
- `feedback-plan` job 只读
- `execute-update` job 只有在 `execute=true`、`allow_run_agent=true` 且 `allow_push_update=true` 时才执行
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
codepilot tool list_files '{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":0}' --trace --runs-dir runs --run-id run-test
```

`--trace` 开启后，命令标准输出仍然会先打印 `ToolResult` JSON，随后再打印 `Trace written to: <path>`。
`--run-id` 可以复用同一个运行目录，`TraceLogger` 会在已有 `trace.jsonl` 的最大 `step` 后继续追加。
`runs/` 目录默认会被忽略，避免把运行时 trace 文件提交到仓库。

### ToolRouter 使用说明

第四步新增了 `ToolRouter`，它会接收一个结构化 `ToolAction`，先写入 `run_start` / `run_end` 事件，再按顺序把动作路由到 traced tool call。

```bash
# 路由一个结构化动作
codepilot route '{"tool_name":"list_files","arguments":{"repo":".","path":".","max_depth":2,"max_entries":200,"offset":0}}'

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
codepilot route '{"tool_name":"list_files","arguments":{"repo":".","path":"src/codepilot","max_depth":2,"max_entries":200,"offset":0}}'

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

## CodePilot Lite 第十七步 - Terminal Dashboard 使用说明

第十七步新增了只读的 `codepilot dashboard` 命令。它只读取 `runs/<run_id>/` 里的现有产物，不会执行 agent、LLM 或 tool，也不会修改任何文件。

### 常用命令

```bash
# 静态总览
codepilot dashboard --runs-dir runs --limit 10 --static

# 查看单个 run 的详情
codepilot dashboard --runs-dir runs --run-id mcp-agent-demo-test --static

# 输出 JSON 索引
codepilot dashboard --runs-dir runs --limit 5 --json

# 输出 JSON 详情
codepilot dashboard --runs-dir runs --run-id mcp-agent-demo-test --json

# 交互式 TUI（需要安装 textual）
codepilot dashboard --runs-dir runs --limit 20 --tui
```

### 参数说明

- `--runs-dir`：run 产物根目录，默认是 `runs`
- `--run-id`：查看单个 run；不传则展示 run 列表
- `--json`：输出稳定 JSON，方便脚本处理
- `--static / --tui`：选择静态 Rich 输出或交互式 Textual 界面
- `--status`：按状态过滤索引
- `--run-type`：按 run 类型过滤索引
- `--watch`：静态模式下持续刷新只读视图

### 使用边界

- 不会执行 agent 或 tool
- 不会调用 LLM
- 不会写 trace、report、patch 或 manifest
- 不会修改 `runs/` 目录里的任何文件

## Interactive Agent TUI

新增 `codepilot tui` 用于启动交互式 Agent TUI，也支持直接运行 `codepilot` 进入同一界面。

### 启动方式

```bash
codepilot tui /path/to/project
codepilot tui /path/to/project --permission-mode manual
codepilot tui /path/to/project --model gpt-4.1-mini --max-steps 12
```

### 常用命令

- `/help`：查看命令列表
- `/sessions`：打开跨项目 SQLite Session Picker
- `/switch <session-id>`：切换 Session（当前 Turn 运行时不可用）
- `/new`：创建新的 SQLite Session
- `/archive`、`/unarchive <session-id>`：归档或恢复 Session
- `/move <path>`：只设置下一次新建 Session 的项目，不改变当前 Session
- `/compact`：在空闲时手动压缩上下文；运行中的 Turn 会拒绝该命令
- `/export-session [path]`：显式导出当前 Session；省略路径时使用默认 exports 目录
- `/status`：查看项目、Git、模型和权限状态
- `/permissions`：查看或切换权限模式
- `/diff`：查看变更摘要
- `/report`：查看 trace / report 路径
- `/new`：准备下一条任务
- `/cancel`：取消当前运行
- `/exit`：退出 TUI

### 会话存储和导出

TUI 使用用户级 SQLite 数据库作为 Session 唯一事实来源；Turn、Attempt、消息、权限、工具调用、摘要和 Artifact 都写入数据库，不依赖 TUI Run 索引或运行中的 trace/report 文件。

只有显式执行 `/export-session` 才会生成导出目录。导出包含 `session.json`、`turns.jsonl`、`messages.jsonl`、`events.jsonl`、`trace.jsonl`、`report.json`、递归 Artifact 文件和带 SHA-256/大小校验的 `manifest.json`。导出失败会清理临时目录。

运行中的 Turn 不能切换、创建、归档、移动或手动 Compact Session；请先等待完成或使用 `/cancel`。缺失项目路径和归档 Session 会以只读方式打开，任务输入框保持禁用。

### 第18步语义说明

这一步把 agent 的结束语义分成了几类，方便你在 TUI、trace 和 report 里区分“只是正常说完了”与“真的完成了代码交付”：

- `message_complete`：普通问候、解释、只读回答等自然文本完成，不代表代码交付成功。
- `task_incomplete`：任务本身看起来需要改代码，但模型只用了自然文本结束，或者证据不足，不能算成功。
- `success`：只在确实完成了修改，并且补齐了测试和 diff 证据后才会出现。
- `partial`：模型明确声明只完成了一部分。
- `failed`：模型明确声明失败。

使用时可以这样理解：

- 如果只是提问、查看项目、解释报错，直接自然文本回复就够了，不需要强行输出 JSON。
- 如果任务需要改代码，必须真的通过编辑工具写入文件，随后跑测试并查看 diff；仅仅在 `finish.changed_files` 里自报文件名，不算证据。
- `git status` 只能帮助观察仓库脏状态，不能单独证明本轮改动。
- TUI 和 report 会把 `message_complete`、`task_incomplete`、`partial` 等状态原样展示出来，方便你判断这次运行到底是“回答完了”还是“任务真的完成了”。

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
