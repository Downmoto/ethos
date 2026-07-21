import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError

from ethos.commands import (
    CommandDispatcher,
    CommandRegistrationError,
    CommandRequest,
    CommandResponse,
    CommandSourceError,
    UnknownCommandError,
)


def request(name: str = "test.echo", source: str = "cli") -> CommandRequest:
    return CommandRequest(
        name=name,
        arguments={"message": "hello"},
        source=source,
        owner_id="owner",
        external_context={"terminal": "local"},
    )


def test_dispatcher_streams_registered_handler_output() -> None:
    dispatcher = CommandDispatcher()
    handled: list[CommandRequest] = []

    async def echo(
        command: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        handled.append(command)
        message = command.arguments["message"]
        assert isinstance(message, str)
        yield CommandResponse(text=message)
        yield CommandResponse(data={"events": 1})

    dispatcher.register("test.echo", echo)

    async def execute() -> list[CommandResponse]:
        return [event async for event in dispatcher.execute(request())]

    events = asyncio.run(execute())

    assert handled == [request()]
    assert events == [
        CommandResponse(text="hello"),
        CommandResponse(data={"events": 1}),
    ]


def test_dispatcher_rejects_duplicate_registration() -> None:
    dispatcher = CommandDispatcher()

    async def handle(
        _command: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse(text="handled")

    dispatcher.register("test.command", handle)

    with pytest.raises(
        CommandRegistrationError,
        match="command already registered: test.command",
    ):
        dispatcher.register("test.command", handle)


def test_dispatcher_rejects_unknown_command() -> None:
    dispatcher = CommandDispatcher()

    async def execute() -> None:
        with pytest.raises(
            UnknownCommandError, match="unknown command: test.missing"
        ):
            _ = [
                event
                async for event in dispatcher.execute(request("test.missing"))
            ]

    asyncio.run(execute())


def test_dispatcher_enforces_allowed_sources() -> None:
    dispatcher = CommandDispatcher()

    async def handle(
        _command: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse(text="handled")

    dispatcher.register(
        "discord.channel.create", handle, allowed_sources={"discord"}
    )

    async def execute() -> None:
        with pytest.raises(
            CommandSourceError,
            match="discord.channel.create cannot be invoked from cli",
        ):
            _ = [
                event
                async for event in dispatcher.execute(
                    request("discord.channel.create")
                )
            ]

        events = [
            event
            async for event in dispatcher.execute(
                request("discord.channel.create", source="discord")
            )
        ]
        assert events == [CommandResponse(text="handled")]

    asyncio.run(execute())


@pytest.mark.parametrize(
    "name",
    ["chat", "Workspace.create", "workspace..create", "workspace_create"],
)
def test_command_request_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        request(name)
