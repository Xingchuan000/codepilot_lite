from codepilot.agent.prompts import SYSTEM_PROMPT, build_initial_messages, build_system_prompt, build_user_prompt


def test_build_initial_messages_returns_system_and_user_messages(tmp_path) -> None:
    messages = build_initial_messages("Fix tests", tmp_path)

    assert [message.role for message in messages] == ["system", "user"]


def test_system_prompt_is_native_only_and_does_not_duplicate_tool_schema() -> None:
    prompt = build_system_prompt()

    assert prompt == SYSTEM_PROMPT
    assert "provided native tools" in prompt
    assert "Do not write tool calls as JSON" in prompt
    assert "codepilot_finish" in prompt
    assert "Available tools:" not in prompt
    assert "tool_name" not in prompt


def test_prompt_requires_explicit_user_authorization_for_write_tools() -> None:
    assert "Only use write tools when the user explicitly requests a modification" in build_system_prompt()


def test_build_user_prompt_contains_task_and_repo(tmp_path) -> None:
    prompt = build_user_prompt("Fix bug", tmp_path)

    assert "Task: Fix bug" in prompt
    assert f"Repository: {tmp_path}" in prompt
