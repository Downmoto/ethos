"""Persistent, workspace-owned conversation sessions.

See ``docs/development/workspaces-and-runtime.md`` for lifecycle, durability,
and concurrency guarantees.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage

from ethos.workspaces import Workspace, WorkspaceManager


class Session(BaseModel):
    """One workspace-scoped conversation and its model history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    workspace_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None
    messages: tuple[ModelMessage, ...] = ()

    @property
    def archived(self) -> bool:
        return self.archived_at is not None


class SessionManager:
    """Validate and persist sessions beneath their owning workspaces.

    File replacement is atomic for readers, but this manager has no
    cross-process lock. Callers must serialise competing updates separately.
    """

    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces

    def create(self, workspace_name: str) -> Session:
        """Create a new active session in a workspace."""
        workspace = self.workspaces.get(workspace_name)
        session = Session(workspace_name=workspace.name)
        self._write(workspace, session, create=True)
        return session

    def get(self, workspace_name: str, session_id: str) -> Session:
        """Load a session without trusting its requested path or stored owner.

        The canonical UUID, filename, and stored workspace must agree. These
        checks prevent renamed or copied records from silently crossing a
        workspace boundary.
        """
        workspace = self.workspaces.get(workspace_name)
        canonical_id = self._validate_id(session_id)
        path = workspace.sessions_path / f"{canonical_id}.json"
        if path.is_symlink():
            raise ValueError(f"session must not be a symlink: {canonical_id}")
        if not path.is_file():
            raise FileNotFoundError(f"session does not exist: {canonical_id}")

        session = Session.model_validate_json(path.read_bytes())
        if str(session.id) != canonical_id:
            raise ValueError(
                f"session ID does not match filename: {canonical_id}"
            )
        if session.workspace_name != workspace.name:
            raise ValueError(
                f"session belongs to another workspace: {canonical_id}"
            )
        return session

    def list(self, workspace_name: str) -> tuple[Session, ...]:
        """List a workspace's sessions in creation order."""
        workspace = self.workspaces.get(workspace_name)
        sessions = [
            self.get(workspace.name, path.stem)
            for path in workspace.sessions_path.iterdir()
            if path.suffix == ".json"
        ]
        return tuple(
            sorted(sessions, key=lambda item: (item.created_at, item.id))
        )

    def archive(self, workspace_name: str, session_id: str) -> Session:
        """Archive a session while preserving its history."""
        workspace = self.workspaces.get(workspace_name)
        session = self.get(workspace.name, session_id)
        if session.archived:
            return session
        archived = session.model_copy(update={"archived_at": datetime.now(UTC)})
        self._write(workspace, archived)
        return archived

    def replace_messages(
        self,
        workspace_name: str,
        session_id: str,
        messages: Iterable[ModelMessage],
    ) -> Session:
        """Atomically replace the history of an active session."""
        workspace = self.workspaces.get(workspace_name)
        session = self.get(workspace.name, session_id)
        if session.archived:
            raise ValueError(f"session is archived: {session_id}")
        updated = session.model_copy(update={"messages": tuple(messages)})
        self._write(workspace, updated)
        return updated

    def _write(
        self, workspace: Workspace, session: Session, *, create: bool = False
    ) -> None:
        """Replace a complete record atomically within its session directory."""
        path = workspace.sessions_path / f"{session.id}.json"
        if create and path.exists():
            raise FileExistsError(f"session already exists: {session.id}")

        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                session.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_id(session_id: str) -> str:
        try:
            parsed = UUID(session_id)
        except ValueError as error:
            raise ValueError(f"invalid session ID: {session_id!r}") from error
        canonical = str(parsed)
        if session_id != canonical:
            raise ValueError(f"invalid session ID: {session_id!r}")
        return canonical
