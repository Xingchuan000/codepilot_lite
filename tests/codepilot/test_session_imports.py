from __future__ import annotations

import importlib

import pytest


def test_session_modules_import_cleanly() -> None:
    importlib.import_module("codepilot.session")
    importlib.import_module("codepilot.session.runtime")
    importlib.import_module("codepilot.agent.loop")
    importlib.import_module("codepilot.router.router")


@pytest.mark.parametrize(
    ("modules",),
    [
        (["codepilot.agent.loop", "codepilot.session.runtime"],),
        (["codepilot.router.router", "codepilot.session.permission"],),
        (["codepilot.cli"],),
    ],
)
def test_import_orders(modules: list[str]) -> None:
    for module in modules:
        importlib.import_module(module)
