from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArgs(_ToolArgs):
    path: str = "."
    max_depth: int = Field(default=2, ge=0)
    include_hidden: bool = False
    max_entries: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ReadFileArgs(_ToolArgs):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=120, ge=1)
    max_chars: int = Field(default=12000, ge=1)


class SearchCodeArgs(_ToolArgs):
    query: str = Field(min_length=1)
    path: str = "."
    file_glob: str = "*"
    max_results: int = Field(default=50, ge=1)
    case_sensitive: bool = False


class RunShellArgs(_ToolArgs):
    command: str
    timeout: int = Field(default=30, ge=1)
    max_output_chars: int = Field(default=12000, ge=1)


class RunTestsArgs(_ToolArgs):
    command: str = "pytest"
    timeout: int = Field(default=60, ge=1)
    max_output_chars: int = Field(default=12000, ge=1)
    max_summary_chars: int = Field(default=6000, ge=1)


class GitStatusArgs(_ToolArgs):
    max_entries: int = Field(default=200, ge=1)


class GitDiffArgs(_ToolArgs):
    path: str | None = None
    staged: bool = False
    include_content: bool = False
    max_lines: int = Field(default=300, ge=1)
    max_chars: int = Field(default=12000, ge=1)


class ApplyPatchArgs(_ToolArgs):
    patch: str
    dry_run: bool = False
    max_preview_chars: int = Field(default=4000, ge=1)


class ReplaceRangeArgs(_ToolArgs):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: str
    dry_run: bool = False
    max_preview_chars: int = Field(default=4000, ge=1)
