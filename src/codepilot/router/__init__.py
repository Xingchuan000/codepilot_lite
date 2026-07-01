"""CodePilot Lite tool routing layer."""

from typing import TYPE_CHECKING

from codepilot.router.actions import ToolAction, ToolRouteResult

if TYPE_CHECKING:
    from codepilot.router.router import ToolRouter as ToolRouter

__all__ = ["ToolAction", "ToolRouteResult", "ToolRouter"]


def __getattr__(name: str):
    if name == "ToolRouter":
        from codepilot.router.router import ToolRouter

        return ToolRouter
    raise AttributeError(name)
