from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolAction


def _decision(tool_name: str, arguments: dict, *, mode: str = "build", approved: bool = False):
    return PolicyChecker.default().check(
        ToolAction(tool_name=tool_name, arguments=arguments),
        context=PolicyContext(mode=mode, approved=approved),
    )


def test_replace_range_safe_path_in_build_mode_asks(tmp_path) -> None:
    decision = _decision(
        "replace_range",
        {"repo": tmp_path, "path": "src/demo.py", "start_line": 1, "end_line": 1, "replacement": "x\n"},
    )

    assert decision.asks is True


def test_replace_range_env_path_denied(tmp_path) -> None:
    decision = _decision(
        "replace_range",
        {"repo": tmp_path, "path": ".env", "start_line": 1, "end_line": 1, "replacement": "x\n"},
    )

    assert decision.denied is True


def test_replace_range_normalized_env_path_denied(tmp_path) -> None:
    decision = _decision(
        "replace_range",
        {"repo": tmp_path, "path": "././.env", "start_line": 1, "end_line": 1, "replacement": "x\n"},
    )

    assert decision.denied is True


def test_replace_range_read_only_mode_denied(tmp_path) -> None:
    decision = _decision(
        "replace_range",
        {"repo": tmp_path, "path": "src/demo.py", "start_line": 1, "end_line": 1, "replacement": "x\n"},
        mode="read_only",
    )

    assert decision.denied is True


def test_apply_patch_safe_path_in_build_mode_asks(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": "diff --git a/src/demo.py b/src/demo.py\n--- a/src/demo.py\n+++ b/src/demo.py\n@@ -1 +1 @@\n-old\n+new\n",
        },
    )

    assert decision.asks is True


def test_apply_patch_env_path_denied(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-A=1\n+A=2\n",
        },
    )

    assert decision.denied is True


def test_apply_patch_secrets_path_denied(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": "diff --git a/secrets/token.txt b/secrets/token.txt\n--- a/secrets/token.txt\n+++ b/secrets/token.txt\n@@ -1 +1 @@\n-old\n+new\n",
        },
    )

    assert decision.denied is True


def test_apply_patch_ssh_path_denied(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": "diff --git a/.ssh/config b/.ssh/config\n--- a/.ssh/config\n+++ b/.ssh/config\n@@ -1 +1 @@\n-old\n+new\n",
        },
    )

    assert decision.denied is True


def test_apply_patch_missing_touched_paths_denied_for_non_empty_patch(tmp_path) -> None:
    decision = _decision("apply_patch", {"repo": tmp_path, "patch": "just text\n"})

    assert decision.denied is True


def test_apply_patch_multiple_files_any_sensitive_path_denies_whole_patch(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": """diff --git a/src/demo.py b/src/demo.py
--- a/src/demo.py
+++ b/src/demo.py
@@ -1 +1 @@
-old
+new
diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1 +1 @@
-A=1
+A=2
""",
        },
    )

    assert decision.denied is True


def test_apply_patch_delete_sensitive_file_denied(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": "diff --git a/.ssh/config b/.ssh/config\n--- a/.ssh/config\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
        },
    )

    assert decision.denied is True


def test_apply_patch_add_sensitive_file_denied(tmp_path) -> None:
    decision = _decision(
        "apply_patch",
        {
            "repo": tmp_path,
            "patch": "diff --git a/.env b/.env\n--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+A=2\n",
        },
    )

    assert decision.denied is True
