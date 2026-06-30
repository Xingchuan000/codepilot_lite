"""CodePilot Lite structured trace logging."""

from codepilot.trace.events import TraceEvent, TraceEventType
from codepilot.trace.logger import TraceLogger, make_run_id

__all__ = ["TraceEvent", "TraceEventType", "TraceLogger", "make_run_id"]
