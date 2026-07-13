from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_router_does_not_depend_on_tui_agent() -> None:
    modules = _imported_modules(ROOT / "src/codepilot/router/router.py")

    assert not any(module.startswith("codepilot.tui_agent") for module in modules)


def test_agent_does_not_depend_on_tui_agent() -> None:
    agent_dir = ROOT / "src/codepilot/agent"
    modules = set()
    for path in agent_dir.glob("*.py"):
        modules.update(_imported_modules(path))

    assert not any(module.startswith("codepilot.tui_agent") for module in modules)


def test_post_pr_models_stays_independent() -> None:
    modules = _imported_modules(ROOT / "src/codepilot/post_pr/models.py")

    assert not any(module.startswith("codepilot.post_pr.controller") for module in modules)
    assert not any(module.startswith("codepilot.cli") for module in modules)
    assert not any(module.startswith("codepilot.tui_agent") for module in modules)


def test_event_reducer_does_not_import_trace_event() -> None:
    modules = _imported_modules(ROOT / "src/codepilot/tui_agent/event_reducer.py")

    assert "codepilot.trace.events" not in modules
