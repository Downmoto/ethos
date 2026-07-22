# pyright: reportUnknownVariableType=false
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import discord
from discord import app_commands
from pydantic import JsonValue, SecretStr

from ethos.commands import CommandRequest, CommandResponse
from ethos.config import DiscordConfig
from ethos.gateways.discord import (
    DiscordGateway,
    _chunks,  # pyright: ignore[reportPrivateUsage]
)


@dataclass
class FakeAuthor:
    id: int = 10
    bot: bool = False


@dataclass
class FakeChannel:
    id: int = 20
    sent: list[str] = field(default_factory=list)

    async def send(self, text: str) -> None:
        self.sent.append(text)


@dataclass
class FakeGuild:
    id: int = 40


@dataclass
class FakeUser:
    id: int = 10


@dataclass
class FakeResponse:
    deferred: bool = False
    sent: list[tuple[str, bool]] = field(default_factory=list)

    async def defer(self, *, thinking: bool) -> None:
        self.deferred = thinking

    async def send_message(self, text: str, *, ephemeral: bool) -> None:
        self.sent.append((text, ephemeral))


@dataclass
class FakeFollowup:
    sent: list[str] = field(default_factory=list)

    async def send(self, text: str) -> None:
        self.sent.append(text)


@dataclass
class FakeInteraction:
    channel_id: int = 20
    guild_id: int | None = 40
    user: FakeUser = field(default_factory=FakeUser)
    response: FakeResponse = field(default_factory=FakeResponse)
    followup: FakeFollowup = field(default_factory=FakeFollowup)


@dataclass
class FakeMessage:
    content: str
    author: FakeAuthor = field(default_factory=FakeAuthor)
    channel: FakeChannel = field(default_factory=FakeChannel)
    id: int = 30
    guild: FakeGuild | None = None
    mentions: list[object] = field(default_factory=list)


def test_discord_registers_universal_slash_commands() -> None:
    async def execute(
        _request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse()

    client = DiscordGateway(
        DiscordConfig(
            token=SecretStr("secret"), allowed_user_ids=frozenset({10})
        )
    ).create_client(execute)

    assert not client.intents.message_content
    assert {command.name for command in client.tree.get_commands()} == {
        "chat",
        "session-archive",
        "session-create",
        "session-list",
        "session-show",
        "workspace-create",
        "workspace-list",
        "workspace-show",
    }


def test_discord_slash_command_translates_interaction() -> None:
    requests: list[CommandRequest] = []

    async def execute(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        requests.append(request)
        yield CommandResponse(text="created")

    client = DiscordGateway(
        DiscordConfig(
            token=SecretStr("secret"), allowed_user_ids=frozenset({10})
        )
    ).create_client(execute)
    fake_interaction = FakeInteraction()
    interaction = cast(discord.Interaction, fake_interaction)
    command = client.tree.get_command("workspace-create")
    assert isinstance(command, app_commands.Command)

    asyncio.run(cast(Any, command.callback)(interaction, "my-project"))

    assert requests == [
        CommandRequest(
            name="workspace.create",
            arguments={"name": "my-project"},
            source="discord",
            owner_id="10",
            external_context={"channel_id": "20", "guild_id": "40"},
        )
    ]
    assert fake_interaction.response.deferred
    assert fake_interaction.followup.sent == ["created"]


def test_discord_rejects_unauthorised_slash_command() -> None:
    requests: list[CommandRequest] = []

    async def execute(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        requests.append(request)
        yield CommandResponse()

    client = DiscordGateway(
        DiscordConfig(
            token=SecretStr("secret"), allowed_user_ids=frozenset({10})
        )
    ).create_client(execute)
    fake_interaction = FakeInteraction(user=FakeUser(id=11))
    command = client.tree.get_command("workspace-list")
    assert isinstance(command, app_commands.Command)

    asyncio.run(
        cast(Any, command.callback)(cast(discord.Interaction, fake_interaction))
    )

    assert requests == []
    assert fake_interaction.response.sent == [
        ("You are not authorised to use Ethos.", True)
    ]
    assert not fake_interaction.response.deferred
    assert fake_interaction.followup.sent == []


def test_discord_concurrent_messages_reuse_one_session() -> None:
    requests: list[CommandRequest] = []

    async def execute(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        requests.append(request)
        if request.name == "session.create":
            yield CommandResponse(
                data=cast(
                    dict[str, JsonValue],
                    {"session": {"id": "session-id"}},
                )
            )
        else:
            yield CommandResponse(text="hello ")
            yield CommandResponse(text="there", done=True)

    client = DiscordGateway(
        DiscordConfig(
            token=SecretStr("secret"), allowed_user_ids=frozenset({10})
        )
    ).create_client(execute)
    channel = FakeChannel()

    async def receive_messages() -> None:
        await asyncio.gather(
            client.on_message(
                cast(discord.Message, FakeMessage("first", channel=channel))
            ),
            client.on_message(
                cast(discord.Message, FakeMessage("second", channel=channel))
            ),
        )
        await client.on_message(
            cast(
                discord.Message,
                FakeMessage(
                    "ignored",
                    author=FakeAuthor(bot=True),
                    channel=channel,
                ),
            )
        )
        await client.on_message(
            cast(
                discord.Message,
                FakeMessage(
                    "ignored",
                    author=FakeAuthor(id=11),
                    channel=channel,
                ),
            )
        )
        await client.on_message(
            cast(
                discord.Message,
                FakeMessage("ignored", channel=channel, guild=FakeGuild()),
            )
        )

    asyncio.run(receive_messages())

    assert [request.name for request in requests] == [
        "session.create",
        "session.chat",
        "session.chat",
    ]
    assert requests[0].arguments == {"workspace": "default"}
    assert requests[1].arguments == {
        "workspace": "default",
        "session_id": "session-id",
        "prompt": "first",
    }
    assert requests[2].arguments["prompt"] == "second"
    assert requests[1].external_context == {
        "channel_id": "20",
        "message_id": "30",
    }
    assert channel.sent == ["hello there", "hello there"]


def test_discord_splits_responses_at_message_limit() -> None:
    chunks = _chunks("x" * 4_001)

    assert tuple(map(len, chunks)) == (2_000, 2_000, 1)
    assert "".join(chunks) == "x" * 4_001
