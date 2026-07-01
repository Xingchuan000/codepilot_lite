from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ToolPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class PathPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class CommandPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_prefixes: list[str] = Field(default_factory=list)
    deny_substrings: list[str] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: ToolPolicyConfig = Field(default_factory=ToolPolicyConfig)
    paths: PathPolicyConfig = Field(default_factory=PathPolicyConfig)
    commands: CommandPolicyConfig = Field(default_factory=CommandPolicyConfig)
