import pytest

from codepilot.llm.types import ChatMessage
from codepilot.session.context_budget import ContextBudgetAllocator, ContextBudgetExceeded, ContextItem


def test_context_budget_keeps_atomic_messages_and_blocks_mandatory_overflow() -> None:
    budget = ContextBudgetAllocator(4)
    required = ContextItem("user", (ChatMessage("user", "a" * 12),), 3, True, 100)
    optional = ContextItem("old", (ChatMessage("user", "b" * 20),), 3, False, 1)

    budget.require(required)

    assert budget.try_add(optional) is False
    with pytest.raises(ContextBudgetExceeded, match="mandatory context item"):
        budget.require(ContextItem("too-large", (ChatMessage("user", "c"),), 2, True, 100))
