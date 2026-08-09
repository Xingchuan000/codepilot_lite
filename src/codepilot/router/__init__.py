"""CodePilot Lite tool routing layer."""

from typing import TYPE_CHECKING

from codepilot.router.actions import ToolAction, ToolRouteResult
from codepilot.router.runtime_tools import RuntimeToolRegistry

if TYPE_CHECKING:
    from codepilot.router.router import ToolRouter as ToolRouter

__all__ = ["RuntimeToolRegistry", "ToolAction", "ToolRouteResult", "ToolRouter"]


def __getattr__(name: str):
    if name == "ToolRouter":
        from codepilot.router.router import ToolRouter

        return ToolRouter
    raise AttributeError(name)
