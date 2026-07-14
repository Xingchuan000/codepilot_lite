from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from minisweagent.config import get_config_from_spec
from minisweagent.models import get_model
from minisweagent.utils.serialize import recursive_merge


@dataclass(frozen=True)
class BuiltLLM:
    """模型构建结果，携带后续 Turn 需要保存的 Provider/Model 身份。"""

    client: object
    provider: str
    model: str


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
        client = FakeLLMClient.from_jsonl(fake_actions)
        return BuiltLLM(client, "fake", client.model)
    built_model = build_swe_model_from_config_specs(model_config or [], model_name=model)
    client = SweAgentModelAdapter(model=built_model)
    configured_model = getattr(getattr(built_model, "config", None), "model_name", None) or model or "default"
    provider = getattr(getattr(built_model, "config", None), "provider", None) or configured_model.split("/", 1)[0]
    return BuiltLLM(client, str(provider), str(configured_model))


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
    mcp_config: str | Path | None = None,
    runs_dir: str | Path = "runs",
    run_id: str | None = None,
) -> AgentRunResult:
    """执行一次最小 agent 任务，并只返回 AgentRunResult。"""

    repo_path = Path(repo).expanduser().resolve()
    built_llm = build_codepilot_llm(fake_actions=fake_actions, model=model, model_config=model_config)
    policy_context = PolicyContext(repo=repo_path, mode=policy_mode, approved=approve, interactive=False)
    mcp_registry = MCPToolRegistry.from_config(mcp_config) if mcp_config else None
    extra_specs = {spec.name: spec for spec in mcp_registry.list_specs()} if mcp_registry else {}
    # 这里必须通过 ToolRouter.from_runs_dir(...) 统一创建 TraceLogger，
    # 这样 ToolRouter 与 MinimalAgentLoop 才会共享同一份 trace.jsonl。
    router = ToolRouter.from_runs_dir(
        runs_dir=runs_dir,
        run_id=run_id,
        policy_checker=PolicyChecker.default(extra_tool_specs=extra_specs),
        policy_context=policy_context,
        external_tool_registry=mcp_registry,
    )
    return MinimalAgentLoop(
        llm=built_llm.client,
        router=router,
        max_steps=max_steps,
        prompt_extra_tool_specs=mcp_registry.list_exposed_specs() if mcp_registry else None,
    ).run(task=task, repo=repo_path)
