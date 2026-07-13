from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(frozen=True)
class SessionPaths:
    """统一描述 Session 持久化相关目录。

    这里故意只负责路径计算，不负责创建目录，避免把副作用散落到调用方看不见的地方。
    """

    data_dir: Path
    database_path: Path
    sessions_dir: Path
    exports_dir: Path


def resolve_session_paths(base_dir: Path | None = None) -> SessionPaths:
    """解析用户级 Session 目录。

    测试时允许显式传入 `tmp_path`，这样就不会碰真实用户目录。
    """

    data_dir = base_dir or Path(user_data_path("codepilot"))
    return SessionPaths(
        data_dir=data_dir,
        database_path=data_dir / "sessions.sqlite3",
        sessions_dir=data_dir / "sessions",
        exports_dir=data_dir / "exports",
    )
