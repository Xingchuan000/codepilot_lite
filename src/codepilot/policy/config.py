from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PathPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deny: list[str] = Field(default_factory=list)


class CommandPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hard_deny_patterns: list[str] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: PathPolicyConfig = Field(default_factory=PathPolicyConfig)
    commands: CommandPolicyConfig = Field(default_factory=CommandPolicyConfig)
