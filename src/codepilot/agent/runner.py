from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.session.model_capabilities import ModelCapabilities, resolve_model_capabilities


@dataclass(frozen=True)
class BuiltLLM:
    """模型构建结果，携带后续 Turn 需要保存的 Provider/Model 身份。"""

    client: object
    provider: str
    model: str
    capabilities: ModelCapabilities | None = None


@dataclass(frozen=True)
class ResolvedModelIdentity:
    """只描述模型身份，不创建 Provider 客户端。"""

    provider: str
    model: str
    model_class: str | None
    source: str


class ModelConfigurationRequired(ValueError):
    """没有任何可执行模型配置时，由调用方展示的明确错误。"""


class ModelIdentityMismatch(ValueError):
    """配置声明的身份与实际构造出的模型身份不一致。"""


def get_model(*args, **kwargs):
    """延迟导入真实模型工厂，保证身份解析不会加载 Provider。"""

    from minisweagent.models import get_model as model_factory

    return model_factory(*args, **kwargs)


def get_minisweagent_model_names() -> tuple[str, ...]:
    """读取 mini-swe-agent 当前可用的模型名称，不构造模型客户端。

    当前 mini-swe-agent 的公开配置接口是单个默认模型 `get_model_name()`；
    因此这里返回单元素元组，为后续 mini-swe-agent 扩展多模型配置保留稳定的
    TUI 只读入口。模型的新增、删除和默认顺序仍完全由 mini-swe-agent 管理。
    """

    from minisweagent.models import get_model_name

    try:
        return (get_model_name(),)
    except ValueError:
        return ()


def _merge_config(base: dict, update: dict) -> dict:
    """递归合并身份解析所需的最小配置结构。"""

    merged = dict(base)
    for key, value in update.items():
        merged[key] = _merge_config(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def _read_config_spec(spec: str | Path) -> dict:
    """读取模型配置规格，不依赖 mini-SWE-agent 的模型包初始化。"""

    if isinstance(spec, str) and "=" in spec:
        key, value = spec.split("=", 1)
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        result: dict = {}
        current = result
        keys = key.split(".")
        for part in keys[:-1]:
            current[part] = {}
            current = current[part]
        current[keys[-1]] = value
        return result
    path = Path(spec)
    if path.suffix != ".yaml":
        path = path.with_suffix(".yaml")
    candidates = [
        Path(spec),
        path,
        Path(os.getenv("MSWEA_CONFIG_DIR", ".")) / path,
        Path(__file__).parents[2] / "minisweagent" / "config" / path,
        Path(__file__).parents[2] / "minisweagent" / "config" / "extra" / path,
        Path(__file__).parents[2] / "minisweagent" / "config" / "benchmarks" / path,
    ]
    config_path = next(candidate for candidate in candidates if candidate.exists())
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _provider_from_model(model: str) -> str:
    """从规范化模型名推导 Provider；只处理模型身份，不访问 Provider。"""

    prefix = model.split("/", 1)[0].lower()
    if "/" in model:
        return prefix.lstrip("@")
    if prefix.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if prefix.startswith(("claude", "sonnet", "opus", "haiku")):
        return "anthropic"
    if prefix.startswith(("gemini", "gemma")):
        return "google"
    return prefix


def resolve_codepilot_model_identity(
    *,
    fake_actions: str | Path | None,
    model: str | None,
    model_config: list[str],
    environ: Mapping[str, str] | None = None,
) -> ResolvedModelIdentity:
    """按固定优先级解析模型身份，绝不导入或初始化真实模型。

    解析顺序必须与实际构造模型使用的配置顺序一致：显式 CLI 模型、配置
    中的 ``model.model_name``、最后才是环境变量。配置合并只读取普通配置
    文件或键值规格，因此新建 Session 不会触发凭据检查、网络请求或模型导入。
    """

    if fake_actions is not None:
        return ResolvedModelIdentity("fake", "fake", "fake", "fake_actions")
    merged: dict = {}
    for spec in model_config:
        merged = _merge_config(merged, _read_config_spec(spec))
    configured = merged.get("model", {})
    environment = os.environ if environ is None else environ
    resolved_model = model or configured.get("model_name")
    source = "cli" if model else "config" if configured.get("model_name") else ""
    if not resolved_model and environ is not None:
        resolved_model = environment.get("MSWEA_MODEL_NAME")
        source = "environment"
    if not resolved_model and environ is None:
        try:
            from minisweagent.models import get_model_name

            resolved_model = get_model_name()
            source = "minisweagent_default"
        except ValueError:
            resolved_model = None
    if not isinstance(resolved_model, str) or not resolved_model:
        raise ModelConfigurationRequired(
            "尚未配置模型。请使用 --model、model config 或 MSWEA_MODEL_NAME 后再新建 Session。"
        )
    provider = configured.get("provider")
    provider = str(provider).lstrip("@") if provider else _provider_from_model(resolved_model)
    model_class = configured.get("model_class")
    return ResolvedModelIdentity(provider, resolved_model, str(model_class) if model_class else None, source)


def build_swe_model_from_config_specs(model_config: list[str], model_name: str | None = None):
    """复用 mini-SWE-agent 的配置拼装逻辑构造模型对象。"""

    from minisweagent.config import get_config_from_spec
    from minisweagent.utils.serialize import recursive_merge

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

    identity = resolve_codepilot_model_identity(
        fake_actions=fake_actions,
        model=model,
        model_config=model_config or [],
    )
    if fake_actions is not None:
        client = FakeLLMClient.from_jsonl(fake_actions)
        return BuiltLLM(client, identity.provider, identity.model, resolve_model_capabilities(identity.provider, identity.model))
    built_model = build_swe_model_from_config_specs(model_config or [], model_name=identity.model)
    client = SweAgentModelAdapter(model=built_model)
    built_config = getattr(built_model, "config", None)
    actual_model = str(getattr(built_config, "model_name", identity.model))
    actual_provider = getattr(built_config, "provider", None) or _provider_from_model(actual_model)
    actual_identity = ResolvedModelIdentity(
        str(actual_provider).lstrip("@"),
        actual_model,
        identity.model_class,
        "built",
    )
    if (actual_identity.provider, actual_identity.model) != (identity.provider, identity.model):
        raise ModelIdentityMismatch(
            f"configured model identity {identity.provider}/{identity.model} does not match "
            f"built model identity {actual_identity.provider}/{actual_identity.model}"
        )
    configured_capabilities = getattr(built_config, "capabilities", None)
    capabilities = configured_capabilities if isinstance(configured_capabilities, ModelCapabilities) else resolve_model_capabilities(actual_identity.provider, actual_identity.model)
    if (capabilities.provider, capabilities.model) != (actual_identity.provider, actual_identity.model):
        raise ModelIdentityMismatch("model capability identity does not match built model identity")
    return BuiltLLM(client, actual_identity.provider, actual_identity.model, capabilities)


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
