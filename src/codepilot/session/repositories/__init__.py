"""Session domain repositories backed by the shared SQLite database."""

from codepilot.session.database import SessionDatabase
from codepilot.session.turn_service import TurnSubmissionService
from codepilot.session.repositories.artifacts import ArtifactRepository
from codepilot.session.repositories.attempts import AttemptRepository
from codepilot.session.repositories.context_summaries import ContextSummaryRepository
from codepilot.session.repositories.events import EventRepository
from codepilot.session.repositories.messages import MessageRepository
from codepilot.session.repositories.permissions import PermissionRepository
from codepilot.session.repositories.projects import ProjectRepository
from codepilot.session.repositories.sessions import SessionRepository
from codepilot.session.repositories.tool_executions import ToolExecutionRepository
from codepilot.session.repositories.turns import TurnRepository


class SessionRepositories:
    """Composition root for session repositories; it exposes no entity facade methods."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database
        self.projects = ProjectRepository(database)
        self.sessions = SessionRepository(database, self.projects)
        self.turns = TurnRepository(database)
        self.attempts = AttemptRepository(database)
        self.messages = MessageRepository(database)
        self.tool_executions = ToolExecutionRepository(database)
        self.permissions = PermissionRepository(database)
        self.events = EventRepository(database)
        self.context_summaries = ContextSummaryRepository(database)
        self.artifacts = ArtifactRepository(database)
        self.turn_submission = TurnSubmissionService(database)

__all__ = [
    "ArtifactRepository",
    "AttemptRepository",
    "ContextSummaryRepository",
    "EventRepository",
    "MessageRepository",
    "PermissionRepository",
    "ProjectRepository",
    "SessionRepositories",
    "SessionRepository",
    "ToolExecutionRepository",
    "TurnRepository",
    "TurnSubmissionService",
]
