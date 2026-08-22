"""Persistent, workspace-scoped conversation sessions.

See ``docs/development/workspaces-and-runtime.md`` for lifecycle, durability,
and concurrency guarantees.
"""

import fcntl
import os
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ethos.models import Message, ToolResultPart
from ethos.tools import ApprovalState, ToolApproval
from ethos.workspaces import Workspace, WorkspaceManager

SESSIONS_DIR: Final = "sessions"


class Session(BaseModel):
    """One workspace-scoped conversation and its model history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    workspace_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None
    messages: tuple[Message, ...] = ()
    approvals: tuple[ToolApproval, ...] = ()

    @property
    def archived(self) -> bool:
        return self.archived_at is not None


class SessionManager:
    """Validate and persist sessions beneath the Ethos home.

    File replacement is atomic for readers. Agent runtimes use the explicit
    per-session file lock to serialise turns across processes.
    """

    def __init__(self, workspaces: WorkspaceManager, root: Path) -> None:
        self.workspaces = workspaces
        self.root = root.expanduser()

    def create(self, workspace_name: str) -> Session:
        """Create a new active session in a workspace."""
        workspace = self.workspaces.get(workspace_name)
        session = Session(workspace_name=workspace.name)
        self._write(workspace, session, create=True)
        return session

    @contextmanager
    def runtime_lock(
        self, workspace_name: str, session_id: str
    ) -> Generator[None]:
        """Exclusively own one session runtime across processes."""
        workspace = self.workspaces.get(workspace_name)
        canonical_id = self._validate_id(session_id)
        directory = self._workspace_path(workspace)
        lock_path = directory / f".{canonical_id}.lock"
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        with os.fdopen(descriptor, "r+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ApprovalStateError(
                    f"session runtime is busy: {canonical_id}"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def get(self, workspace_name: str, session_id: str) -> Session:
        """Load a session without trusting its requested path or stored owner.

        The canonical UUID, filename, and stored workspace must agree. These
        checks prevent renamed or copied records from silently crossing a
        workspace boundary.
        """
        workspace = self.workspaces.get(workspace_name)
        canonical_id = self._validate_id(session_id)
        path = self._workspace_path(workspace) / f"{canonical_id}.json"
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
        directory = self._workspace_path(workspace)
        if not directory.exists():
            return ()
        sessions = [
            self.get(workspace.name, path.stem)
            for path in directory.iterdir()
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
        messages: Iterable[Message],
    ) -> Session:
        """Atomically replace the history of an active session."""
        workspace = self.workspaces.get(workspace_name)
        session = self.get(workspace.name, session_id)
        if session.archived:
            raise ValueError(f"session is archived: {session_id}")
        updated = session.model_copy(update={"messages": tuple(messages)})
        self._write(workspace, updated)
        return updated

    def add_approval(
        self,
        workspace_name: str,
        session_id: str,
        approval: ToolApproval,
    ) -> Session:
        """Persist one new approval request before it is exposed."""
        workspace = self.workspaces.get(workspace_name)
        session = self.get(workspace.name, session_id)
        self._require_active(session)
        if any(item.id == approval.id for item in session.approvals):
            raise ApprovalStateError(
                f"approval request already exists: {approval.id}"
            )
        updated = session.model_copy(
            update={"approvals": (*session.approvals, approval)}
        )
        self._write(workspace, updated)
        return updated

    def get_approval(
        self,
        workspace_name: str,
        session_id: str,
        approval_id: str,
    ) -> ToolApproval:
        session = self.get(workspace_name, session_id)
        return _approval(session, approval_id)

    def transition_approval(
        self,
        workspace_name: str,
        session_id: str,
        approval_id: str,
        *,
        expected: ApprovalState,
        state: ApprovalState,
        result: ToolResultPart | None = None,
        messages: Iterable[Message] | None = None,
    ) -> Session:
        """Atomically consume an approval and optionally checkpoint history."""
        workspace = self.workspaces.get(workspace_name)
        session = self.get(workspace.name, session_id)
        self._require_active(session)
        current = _approval(session, approval_id)
        if current.state is not expected:
            raise ApprovalStateError(
                f"approval request is {current.state.value}: {approval_id}"
            )
        if (expected, state) not in (
            (ApprovalState.PENDING, ApprovalState.EXECUTING),
            (ApprovalState.PENDING, ApprovalState.DENIED),
            (ApprovalState.EXECUTING, ApprovalState.COMPLETED),
        ):
            raise ApprovalStateError(
                "invalid approval transition: "
                f"{expected.value} -> {state.value}"
            )
        try:
            replacement = ToolApproval.model_validate(
                {
                    **current.model_dump(),
                    "state": state,
                    "result": result,
                }
            )
        except ValidationError as error:
            raise ApprovalStateError(
                f"invalid approval transition: {approval_id}"
            ) from error
        approvals = tuple(
            replacement if item.id == approval_id else item
            for item in session.approvals
        )
        updates: dict[str, object] = {"approvals": approvals}
        if messages is not None:
            updates["messages"] = tuple(messages)
        updated = session.model_copy(update=updates)
        self._write(workspace, updated)
        return updated

    def recover_executing_approvals(
        self, workspace_name: str, session_id: str
    ) -> Session:
        """Make interrupted executions permanently non-executable."""
        workspace = self.workspaces.get(workspace_name)
        session = self.get(workspace.name, session_id)
        if not any(
            item.state is ApprovalState.EXECUTING for item in session.approvals
        ):
            return session
        approvals = tuple(
            ToolApproval.model_validate(
                {
                    **item.model_dump(),
                    "state": ApprovalState.INDETERMINATE,
                }
            )
            if item.state is ApprovalState.EXECUTING
            else item
            for item in session.approvals
        )
        recovered = session.model_copy(update={"approvals": approvals})
        self._write(workspace, recovered)
        return recovered

    @staticmethod
    def _require_active(session: Session) -> None:
        if session.archived:
            raise ValueError(f"session is archived: {session.id}")

    def _write(
        self, workspace: Workspace, session: Session, *, create: bool = False
    ) -> None:
        """Replace a complete record atomically within its session directory."""
        directory = self._workspace_path(workspace)
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = directory / f"{session.id}.json"
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

    def _workspace_path(self, workspace: Workspace) -> Path:
        if self.root.is_symlink():
            raise ValueError(
                f"sessions directory must not be a symlink: {self.root}"
            )
        path = self.root / workspace.path.relative_to(self.workspaces.root)
        if path.is_symlink():
            raise ValueError(
                f"workspace sessions must not be a symlink: {workspace.name}"
            )
        return path

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


class ApprovalNotFoundError(FileNotFoundError):
    pass


class ApprovalStateError(RuntimeError):
    pass


def _approval(session: Session, approval_id: str) -> ToolApproval:
    for approval in session.approvals:
        if approval.id == approval_id:
            return approval
    raise ApprovalNotFoundError(
        f"approval request does not exist: {approval_id}"
    )
