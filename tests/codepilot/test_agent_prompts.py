from codepilot.agent.prompts import build_initial_messages, build_system_prompt, build_user_prompt, render_tool_catalog


def test_build_initial_messages_returns_system_and_user_messages(tmp_path) -> None:
    messages = build_initial_messages("Fix tests", tmp_path)

    assert [message.role for message in messages] == ["system", "user"]


def test_system_prompt_contains_required_rules() -> None:
    prompt = build_system_prompt()

    assert "普通问候、概念解释、以及不需要仓库上下文的问题，可以直接用自然文本回复。" in prompt
    assert "需要确认项目事实时，调用读取工具。" in prompt
    assert "不要在没有成功执行写入工具时声称已经修改、修复或实现代码。" in prompt
    assert "tool_call" in prompt
    assert "finish" in prompt
    assert "Prefer structured tools" in prompt
    assert "run_tests" in prompt
    assert "git_status" in prompt
    assert "git_diff" in prompt
    assert "requires more entries" in prompt
    assert "Do not automatically exhaust every page" in prompt
    assert "Do not increase max_entries to bypass pagination." in prompt
    assert "只有用户明确要求修改，或用户在当前对话中明确授权修改时，才调用写入工具。" in prompt
    assert "模型发现修改有必要、认为修改有帮助，不等于用户授权。" in prompt
    assert "IMPORTANT JSON FIELD RULES" in prompt
    assert '"tool_name"' in prompt
    assert '"arguments"' in prompt
    assert "necessary trailing newline" in prompt
    assert "short_rationale" in prompt
    assert '"parameters"' in prompt
    assert '"action"' in prompt
    assert "GOOD:" in prompt
    assert "BAD:" in prompt


def test_render_tool_catalog_reads_registry_and_omits_repo_by_default() -> None:
    catalog = render_tool_catalog()

    assert "read_file" in catalog
    assert "replace_range" in catalog
    assert "run_tests" in catalog
    assert "offset" in catalog
    assert "repo:" not in catalog


def test_prompt_requires_explicit_user_authorization_for_write_tools() -> None:
    prompt = build_system_prompt()

    assert "你已经确认需要修改" not in prompt
    assert "Only use write tools when the user explicitly requests a modification" in prompt


def test_build_user_prompt_contains_task_and_repo(tmp_path) -> None:
    prompt = build_user_prompt("Fix bug", tmp_path)

    assert "Task: Fix bug" in prompt
    assert f"Repository: {tmp_path}" in prompt
