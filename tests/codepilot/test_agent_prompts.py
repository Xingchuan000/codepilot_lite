from codepilot.agent.prompts import build_initial_messages, build_system_prompt, build_user_prompt, render_tool_catalog


def test_build_initial_messages_returns_system_and_user_messages(tmp_path) -> None:
    messages = build_initial_messages("Fix tests", tmp_path)

    assert [message.role for message in messages] == ["system", "user"]


def test_system_prompt_contains_required_rules() -> None:
    prompt = build_system_prompt()

    assert "Return exactly one JSON object" in prompt
    assert "tool_call" in prompt
    assert "finish" in prompt
    assert "Prefer structured tools" in prompt
    assert "run_tests" in prompt
    assert "git_status" in prompt
    assert "git_diff" in prompt


def test_render_tool_catalog_reads_registry_and_omits_repo_by_default() -> None:
    catalog = render_tool_catalog()

    assert "read_file" in catalog
    assert "replace_range" in catalog
    assert "run_tests" in catalog
    assert "repo:" not in catalog


def test_build_user_prompt_contains_task_and_repo(tmp_path) -> None:
    prompt = build_user_prompt("Fix bug", tmp_path)

    assert "Task: Fix bug" in prompt
    assert f"Repository: {tmp_path}" in prompt
