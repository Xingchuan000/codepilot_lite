from __future__ import annotations

from pathlib import Path
from typing import Literal

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from minisweagent.config import get_config_from_spec
from minisweagent.models import get_model
from minisweagent.utils.serialize import recursive_merge


def build_swe_model_from_config_specs(model_config: list[str], model_name: str | None = None):
    """复用 mini-SWE-agent 的配置拼装逻辑构造模型对象。"""

    config: dict = {}
    for spec in model_config:
        config = recursive_merge(config, get_config_from_spec(spec))
    if model_name is not None:
        config = recursive_merge(config, {"model": {"model_name": model_name}})
    return get_model(config=config.get("model", {}))


def build_codepilot_llm(
    *,
    fake_actions: str | Path | None = None,
    model: str | None = None,
    model_config: list[str] | None = None,
):
    """按计划要求在 FakeLLM 与真实模型 adapter 之间切换。"""

    if fake_actions is not None:
        # 第十步要求 fake_actions 非空时直接走固定脚本响应，避免引入真实 LLM 不确定性。
        return FakeLLMClient.from_jsonl(fake_actions)
    return SweAgentModelAdapter(model=build_swe_model_from_config_specs(model_config or [], model_name=model))


def run_agent_task(
    *,
    task: str,
    repo: str | Path,
    max_steps: int = 12,
    policy_mode: Literal["read_only", "build", "danger"] = "build",
    approve: bool = False,
    fake_actions: str | Path | None = None,
    model: str | None = None,
    model_config: list[str] | None = None,
    runs_dir: str | Path = "runs",
    run_id: str | None = None,
) -> AgentRunResult:
    """执行一次最小 agent 任务，并只返回 AgentRunResult。"""

    repo_path = Path(repo).expanduser().resolve()
    llm = build_codepilot_llm(fake_actions=fake_actions, model=model, model_config=model_config)
    policy_context = PolicyContext(repo=repo_path, mode=policy_mode, approved=approve, interactive=False)
    # 这里必须通过 ToolRouter.from_runs_dir(...) 统一创建 TraceLogger，
    # 这样 ToolRouter 与 MinimalAgentLoop 才会共享同一份 trace.jsonl。
    router = ToolRouter.from_runs_dir(
        runs_dir=runs_dir,
        run_id=run_id,
        policy_checker=PolicyChecker.default(),
        policy_context=policy_context,
    )
    return MinimalAgentLoop(llm=llm, router=router, max_steps=max_steps).run(task=task, repo=repo_path)
