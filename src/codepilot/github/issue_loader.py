from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import error, request

from codepilot.github.issue_models import IssueRef, IssueTask

_GITHUB_ISSUE_URL_RE = re.compile(r"^(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)(?:[/?#].*)?$")


def load_issue_from_file(path: str | Path) -> IssueTask:
    """从本地 Markdown 文件加载 issue。"""

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if not file_path.is_file():
        raise ValueError(f"Issue path is not a file: {file_path}")
    content = file_path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"Issue markdown is empty: {file_path}")

    lines = content.splitlines()
    title_line_index: int | None = None
    title: str | None = None
    for index, line in enumerate(lines):
        # 只接受单个 `# ` 开头的一行作为一级标题，严格避免把二级标题误识别为 title。
        if line.startswith("# "):
            title_line_index = index
            title = line[2:].strip() or file_path.stem
            break
    if title_line_index is None:
        title = file_path.stem
        body = content
    else:
        body = "\n".join(lines[:title_line_index] + lines[title_line_index + 1 :]).strip("\n")

    return IssueTask(
        title=title,
        body=body,
        ref=IssueRef(source="file", file_path=str(file_path)),
        metadata={"format": "markdown"},
    )


def parse_github_issue_url(url: str) -> IssueRef:
    """把 GitHub issue URL 解析成结构化引用。"""

    match = _GITHUB_ISSUE_URL_RE.match(url)
    if match is None:
        raise ValueError(f"Invalid GitHub issue URL: {url}")
    owner, repo, number = match.groups()
    return IssueRef(source="github", url=url, owner=owner, repo=repo, number=int(number))


def load_issue_from_github(url: str, *, token: str | None = None, timeout: int = 20) -> IssueTask:
    """通过 GitHub REST API 拉取 issue 标题、正文和非敏感元数据。"""

    ref = parse_github_issue_url(url)
    api_url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/issues/{ref.number}"
    headers = {"User-Agent": "CodePilot-Lite"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(api_url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise ValueError(f"Failed to load GitHub issue: HTTP {exc.code}. {detail[:300]}") from exc
    except error.URLError as exc:
        raise ValueError(f"Failed to load GitHub issue: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Failed to decode GitHub issue response as JSON.") from exc

    if isinstance(data, dict) and "pull_request" in data:
        raise ValueError("GitHub URL points to a pull request, not an issue.")
    if not isinstance(data, dict):
        raise ValueError("GitHub issue response must be a JSON object.")

    labels: list[str] = []
    for item in data.get("labels", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            labels.append(item["name"])

    return IssueTask(
        title=data.get("title") or f"Issue #{ref.number}",
        body=data.get("body") or "",
        ref=ref,
        labels=labels,
        metadata={
            key: data[key]
            for key in ("state", "html_url", "created_at", "updated_at")
            if key in data and isinstance(data[key], str)
        },
    )
