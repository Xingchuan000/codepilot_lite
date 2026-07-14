from codepilot.session.context_budget import ContextBudgetAllocator


def test_context_budget_is_global_and_marks_truncation() -> None:
    budget = ContextBudgetAllocator(4)
    assert budget.consume_message("a" * 12) == "a" * 12
    assert budget.remaining_chars() == 4
    assert budget.consume_message("b" * 20) == "b" * 4
    assert budget.remaining_chars() == 0
