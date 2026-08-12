from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop
from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.capabilities import ModelCapabilities, resolve_model_capabilities
from codepilot.llm.litellm_native import LiteLLMNativeClient
from codepilot.llm.model_capabilities import resolve_litellm_model_capabilities
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter


@dataclass(frozen=True)
class BuiltLLM:
    """模型构建结果，携带后续 Turn 需要保存的 Provider/Model 身份。"""

    client: object
    provider: str
    model: str
    capabilities: ModelCapabilities | None = None


@dataclass(frozen=True)
class ResolvedLiteLLMConfig:
    model_name: str
    model_kwargs: dict[str, Any]
    model_capabilities: dict[str, Any]


@dataclass(frozen=True)
class ResolvedModelIdentity:
    """只描述模型身份，不创建 Provider 客户端。"""

    provider: str
    model: str
    source: str


class ModelConfigurationRequired(ValueError):
    """没有任何可执行模型配置时，由调用方展示的明确错误。"""


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
    fake_responses: str | Path | None,
    model: str | None,
    model_config: list[str],
    environ: Mapping[str, str] | None = None,
) -> ResolvedModelIdentity:
    """按固定优先级解析模型身份，绝不导入或初始化真实模型。

    解析顺序必须与实际构造模型使用的配置顺序一致：显式 CLI 模型、配置
    中的 ``model.model_name``、最后才是环境变量。配置合并只读取普通配置
    文件或键值规格，因此新建 Session 不会触发凭据检查、网络请求或模型导入。
    """

    if fake_responses is not None:
        return ResolvedModelIdentity("fake", "fake", "fake_responses")
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
    if configured.get("model_class"):
        raise ModelConfigurationRequired(
            "model_class is no longer supported by CodePilot; configure a LiteLLM model name instead"
        )
    provider = configured.get("provider")
    provider = str(provider).lstrip("@") if provider else _provider_from_model(resolved_model)
    return ResolvedModelIdentity(provider, resolved_model, source)


def resolve_litellm_config(
    *,
    model: str | None,
    model_config: list[str],
    environ: Mapping[str, str] | None = None,
) -> ResolvedLiteLLMConfig:
    merged: dict = {}
    for spec in model_config:
        merged = _merge_config(merged, _read_config_spec(spec))
    configured = merged.get("model", {})
    if configured.get("model_class"):
        raise ModelConfigurationRequired(
            "model_class is no longer supported by CodePilot; configure a LiteLLM model name instead"
        )

    environment = os.environ if environ is None else environ
    model_name = model or configured.get("model_name") or environment.get("MSWEA_MODEL_NAME")
    if not isinstance(model_name, str) or not model_name:
        raise ModelConfigurationRequired(
            "尚未配置模型。请使用 --model、model config 或 MSWEA_MODEL_NAME 后再新建 Session。"
        )

    model_kwargs = dict(configured.get("model_kwargs") or {})
    if model_kwargs.pop("drop_params", False):
        raise ModelConfigurationRequired("drop_params=true cannot be used with CodePilot native tool calling")
    return ResolvedLiteLLMConfig(
        model_name=model_name,
        model_kwargs=model_kwargs,
        model_capabilities=dict(merged.get("model_capabilities") or {}),
    )


def _configured_model_capabilities(
    provider: str,
    model: str,
    values: dict[str, Any],
) -> ModelCapabilities | None:
    if not values:
        return None
    max_input = values["max_input_tokens"]
    if not isinstance(max_input, int) or max_input <= 0:
        raise ValueError("model_capabilities.max_input_tokens must be a positive integer")
    max_output = values.get("max_output_tokens")
    if not isinstance(max_output, int) or max_output <= 0:
        max_output = min(16_384, max(4_096, max_input // 8))
    return ModelCapabilities(
        provider=provider,
        model=model,
        max_context_tokens=max_input,
        max_output_tokens=max_output,
        reasoning_format=values.get("reasoning_format"),
        supports_reasoning_replay=bool(values.get("supports_reasoning_replay", False)),
        source="config",
    )


def _load_minisweagent_config() -> None:
    from dotenv import load_dotenv

    from minisweagent import global_config_file

    load_dotenv(dotenv_path=global_config_file)


def build_codepilot_llm(
    *,
    fake_responses: str | Path | None = None,
    model: str | None = None,
    model_config: list[str] | None = None,
):
    """构造结构化测试模型或唯一的 LiteLLM Native 模型客户端。"""

    if fake_responses is not None:
        identity = resolve_codepilot_model_identity(
            fake_responses=fake_responses,
            model=model,
            model_config=model_config or [],
        )
        client = StructuredFakeLLM.from_jsonl(fake_responses)
        return BuiltLLM(client, identity.provider, identity.model, resolve_model_capabilities(identity.provider, identity.model))
    _load_minisweagent_config()
    identity = resolve_codepilot_model_identity(
        fake_responses=fake_responses,
        model=model,
        model_config=model_config or [],
    )
    config = resolve_litellm_config(model=model, model_config=model_config or [])
    client = LiteLLMNativeClient(model_name=config.model_name, model_kwargs=config.model_kwargs)
    capabilities = (
        _configured_model_capabilities(identity.provider, config.model_name, config.model_capabilities)
        or resolve_litellm_model_capabilities(provider=identity.provider, model=config.model_name)
        or resolve_model_capabilities(identity.provider, config.model_name)
    )
    return BuiltLLM(client, identity.provider, config.model_name, capabilities)


def run_agent_task(
    *,
    task: str,
    repo: str | Path,
    max_steps: int = 12,
    policy_mode: Literal["read_only", "build", "danger"] = "build",
    fake_responses: str | Path | None = None,
    model: str | None = None,
    model_config: list[str] | None = None,
    mcp_config: str | Path | None = None,
    runs_dir: str | Path = "runs",
    run_id: str | None = None,
) -> AgentRunResult:
    """执行一次最小 agent 任务，并只返回 AgentRunResult。"""

    repo_path = Path(repo).expanduser().resolve()
    built_llm = build_codepilot_llm(fake_responses=fake_responses, model=model, model_config=model_config)
    policy_context = PolicyContext(repo=repo_path, mode=policy_mode, interactive=False)
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
