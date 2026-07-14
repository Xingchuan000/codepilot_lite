"""Deprecated import location for the TUI Session controller.

The TUI no longer defines a second SessionStore.  This alias is retained only
for callers that have not migrated their import yet.
"""

from __future__ import annotations

from codepilot.tui_agent.session_controller import SessionController as SessionStore
from codepilot.tui_agent.session_controller import now_iso

__all__ = ["SessionStore", "now_iso"]
