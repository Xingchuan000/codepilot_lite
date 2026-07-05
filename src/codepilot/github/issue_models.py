from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class IssueRef(BaseModel):
    """记录 issue 来源，保证文件来源和 GitHub 来源使用统一结构。"""

    source: Literal["file", "github"]
    url: str | None = None
    file_path: str | None = None
    owner: str | None = None
    repo: str | None = None
    number: int | None = None


class IssueTask(BaseModel):
    """把外部 issue 输入整理成 agent workflow 可直接消费的任务对象。"""

    title: str
    body: str
    ref: IssueRef
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
