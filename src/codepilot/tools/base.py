"""CodePilot Lite 工具基础类型。

这一层只负责描述工具的风险、权限和标准返回结构，不负责具体执行逻辑。
后续无论是 PolicyChecker 还是 TraceLogger，都可以直接复用这里的数据结构。
"""

from enum import Enum
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field


class ToolRisk(str, Enum):
    """工具风险等级。

    使用字符串枚举，方便直接序列化到 JSON / 日志 / trace 元数据中。
    """

    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    LOCAL_EXECUTION = "local_execution"
    SHELL_EXECUTION = "shell_execution"
    NETWORK = "network"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class ToolSideEffect(str, Enum):
    """工具副作用类型。

    这个维度比风险更细，后续可以用来做更精确的权限或审计判断。
    """

    NONE = "none"
    LOCAL_WRITE = "local_write"
    LOCAL_EXEC = "local_exec"
    NETWORK = "network"
    EXTERNAL = "external"


class DefaultPermission(str, Enum):
    """工具默认权限。

    allow / ask / deny 三档足够支撑第二步的工具层和后续权限策略。
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ToolResult(BaseModel):
    """所有工具的统一返回结果。

    success 用于快速判断执行是否成功，output / error 负责承载可读信息，
    metadata 则给后续 trace、权限和调试留出扩展空间。
    """

    success: bool
    output: str = ""
    output_summary: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """工具的结构化说明。

    这里刻意保持很薄：只描述工具，不尝试封装执行器或路由器。
    """

    name: str
    description: str
    risk: ToolRisk
    side_effect: ToolSideEffect
    default_permission: DefaultPermission
    parameters: dict[str, Any] = Field(default_factory=dict)


def elapsed_ms(start: float) -> int:
    """把秒级耗时换算成毫秒，方便统一记录到 metadata 里。"""

    return int((perf_counter() - start) * 1000)
