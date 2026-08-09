from __future__ import annotations

from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router.actions import ToolAction


def test_structured_write_scope_allows_inside_and_denies_outside(tmp_path: Path) -> None:
    checker = PolicyChecker.default()
    context = PolicyContext(repo=tmp_path, mode="build", approved=True, metadata={"write_scope": ["src/**"]})

    allowed = checker.check(
        ToolAction(tool_name="replace_range", arguments={"path": "src/app.py", "start_line": 1, "end_line": 1, "replacement": "pass"}),
        context=context,
    )
    denied = checker.check(
        ToolAction(tool_name="replace_range", arguments={"path": "README.md", "start_line": 1, "end_line": 1, "replacement": "unsafe"}),
        context=context,
    )

    assert allowed.denied is False
    assert denied.denied is True
    assert denied.matched_rule == "agent.profile.write_scope.deny"


def test_apply_patch_scope_is_all_or_nothing(tmp_path: Path) -> None:
    checker = PolicyChecker.default()
    context = PolicyContext(repo=tmp_path, mode="build", approved=True, metadata={"write_scope": ["src/**"]})
    patch = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-old
+new
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""

    decision = checker.check(ToolAction(tool_name="apply_patch", arguments={"patch": patch}), context=context)

    assert decision.denied is True
    assert decision.matched_rule == "agent.profile.write_scope.deny"


def test_write_scope_does_not_make_shell_available() -> None:
    checker = PolicyChecker.default()
    context = PolicyContext(mode="build", approved=True, metadata={"allowed_tools": ["replace_range"], "write_scope": ["src/**"]})

    decision = checker.check(ToolAction(tool_name="run_shell", arguments={"command": "touch src/app.py"}), context=context)

    assert decision.denied is True
    assert decision.matched_rule == "agent.profile.tool.deny"
