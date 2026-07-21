"""Asynchronous in-process command dispatch."""

import re
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass

from ethos.commands.models import (
    COMMAND_NAME_PATTERN,
    CommandRequest,
    CommandResponse,
)

type CommandHandler = Callable[[CommandRequest], AsyncIterator[CommandResponse]]


class CommandRegistrationError(ValueError):
    """Raised when a command cannot be registered."""


class UnknownCommandError(LookupError):
    """Raised when no handler is registered for a command."""


class CommandSourceError(PermissionError):
    """Raised when a command does not permit its invocation source."""


@dataclass(frozen=True)
class _Registration:
    handler: CommandHandler
    allowed_sources: frozenset[str] | None


class CommandDispatcher:
    """Register command handlers and stream their output."""

    def __init__(self) -> None:
        self._commands: dict[str, _Registration] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        allowed_sources: Iterable[str] | None = None,
    ) -> None:
        """Register one handler, optionally restricted by source."""
        if re.fullmatch(COMMAND_NAME_PATTERN, name) is None:
            raise CommandRegistrationError(f"invalid command name: {name}")
        if name in self._commands:
            raise CommandRegistrationError(
                f"command already registered: {name}"
            )

        sources = (
            frozenset(allowed_sources) if allowed_sources is not None else None
        )
        if sources is not None and (
            not sources
            or any(
                not source.strip() or source != source.strip()
                for source in sources
            )
        ):
            raise CommandRegistrationError(
                "allowed command sources must be non-empty identifiers"
            )

        self._commands[name] = _Registration(handler, sources)

    async def execute(
        self, request: CommandRequest
    ) -> AsyncIterator[CommandResponse]:
        """Dispatch a request and stream its handler's output."""
        registration = self._commands.get(request.name)
        if registration is None:
            raise UnknownCommandError(f"unknown command: {request.name}")
        if (
            registration.allowed_sources is not None
            and request.source not in registration.allowed_sources
        ):
            raise CommandSourceError(
                f"{request.name} cannot be invoked from {request.source}"
            )

        async for event in registration.handler(request):
            yield event
