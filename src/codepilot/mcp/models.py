from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MCPTransport = Literal["fake", "stdio"]
MCPToolSideEffectHint = Literal["read_only", "local_write", "local_exec", "network", "external", "unknown"]
MCPToolStatus = Literal["available", "disabled", "failed"]
MCPServerTrustLevel = Literal["fake", "local_trusted", "local_untrusted", "remote_untrusted"]
MCPServerInstructionsPolicy = Literal["ignore", "record_summary", "inject_as_untrusted_context"]


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    transport: MCPTransport = "fake"
    command: list[str] = Field(default_factory=list)
    cwd: Path | None = None
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tool_allowlist: list[str] = Field(default_factory=list)
    tool_denylist: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    max_output_chars: int = 12000
    trust_level: MCPServerTrustLevel = "fake"
    expose_to_agent: bool = True
    require_tool_allowlist: bool = True
    trusted_annotations: bool = False
    server_instructions_policy: MCPServerInstructionsPolicy = "record_summary"
    startup_timeout_seconds: int = 10
    tool_timeout_seconds: int = 30
    required: bool = False
    max_tools_to_expose: int = 20
    max_description_chars: int = 500

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value

    @field_validator(
        "timeout_seconds",
        "max_output_chars",
        "startup_timeout_seconds",
        "tool_timeout_seconds",
        "max_tools_to_expose",
        "max_description_chars",
    )
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be a positive integer")
        return value

    @model_validator(mode="after")
    def _validate_trust(self) -> "MCPServerConfig":
        if self.trusted_annotations and self.trust_level not in {"fake", "local_trusted"}:
            raise ValueError("trusted_annotations requires trust_level fake or local_trusted")
        return self


class MCPToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_name: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    side_effect_hint: MCPToolSideEffectHint = "unknown"
    annotations: dict[str, Any] = Field(default_factory=dict)
    descriptor_hash: str | None = None
    server_instructions_summary: str | None = None


class MCPToolBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_name: str
    mcp_tool_name: str
    codepilot_tool_name: str
    status: MCPToolStatus = "available"
    reason: str | None = None
    exposed_to_agent: bool = False
    descriptor_hash: str | None = None
    risk_source: str = "heuristic"
    transport: MCPTransport = "fake"
    trust_level: MCPServerTrustLevel = "fake"
    config_hash: str | None = None


class MCPCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_name: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 30
    max_output_chars: int = 12000


class MCPCallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    content: str = ""
    structured_content: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
