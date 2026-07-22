"""Discord adapter for Ethos commands and chat sessions."""

# pyright: reportUnusedFunction=false
import asyncio
from collections import defaultdict
from typing import Final

import discord
from discord import app_commands
from pydantic import JsonValue

from ethos.commands import CommandRequest, CommandResponse
from ethos.config import DiscordConfig
from ethos.gateways.base import CommandExecutor, Gateway
from ethos.workspaces import DEFAULT_WORKSPACE

_SOURCE: Final = "discord"
_MESSAGE_LIMIT: Final = 2_000


def _chunks(text: str) -> tuple[str, ...]:
    value = text or "Done."
    return tuple(
        value[index : index + _MESSAGE_LIMIT]
        for index in range(0, len(value), _MESSAGE_LIMIT)
    )


class _DiscordClient(discord.Client):
    def __init__(
        self, execute: CommandExecutor, allowed_user_ids: frozenset[int]
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._execute_command = execute
        self._allowed_user_ids = allowed_user_ids
        self._bindings: dict[int, tuple[str, str]] = {}
        self._channel_locks: defaultdict[int, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )
        self._register_commands()

    async def setup_hook(self) -> None:
        await self.tree.sync()

    def _request(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        owner_id: str,
        channel_id: int,
        guild_id: int | None,
        message_id: int | None = None,
    ) -> CommandRequest:
        context = {"channel_id": str(channel_id)}
        if guild_id is not None:
            context["guild_id"] = str(guild_id)
        if message_id is not None:
            context["message_id"] = str(message_id)
        return CommandRequest(
            name=name,
            arguments=arguments,
            source=_SOURCE,
            owner_id=owner_id,
            external_context=context,
        )

    async def _execute(
        self, request: CommandRequest
    ) -> tuple[CommandResponse, ...]:
        return tuple(
            [response async for response in self._execute_command(request)]
        )

    async def _respond(
        self, interaction: discord.Interaction, request: CommandRequest
    ) -> tuple[CommandResponse, ...]:
        if interaction.user.id not in self._allowed_user_ids:
            await interaction.response.send_message(
                "You are not authorised to use Ethos.", ephemeral=True
            )
            return ()
        await interaction.response.defer(thinking=True)
        try:
            responses = await self._execute(request)
        except (FileExistsError, FileNotFoundError, ValueError) as error:
            await interaction.followup.send(str(error))
            return ()
        text = "".join(response.text for response in responses)
        for chunk in _chunks(text):
            await interaction.followup.send(chunk)
        return responses

    def _interaction_request(
        self,
        interaction: discord.Interaction,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> CommandRequest:
        if interaction.channel_id is None:
            raise ValueError("Discord interaction has no channel")
        return self._request(
            name,
            arguments,
            owner_id=str(interaction.user.id),
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
        )

    def _register_commands(self) -> None:
        @self.tree.command(
            name="workspace-create", description="Create an Ethos workspace"
        )
        async def workspace_create(
            interaction: discord.Interaction, name: str
        ) -> None:
            await self._respond(
                interaction,
                self._interaction_request(
                    interaction, "workspace.create", {"name": name}
                ),
            )

        @self.tree.command(
            name="workspace-list", description="List Ethos workspaces"
        )
        async def workspace_list(interaction: discord.Interaction) -> None:
            await self._respond(
                interaction,
                self._interaction_request(interaction, "workspace.list", {}),
            )

        @self.tree.command(
            name="workspace-show", description="Show an Ethos workspace"
        )
        async def workspace_show(
            interaction: discord.Interaction, name: str
        ) -> None:
            await self._respond(
                interaction,
                self._interaction_request(
                    interaction, "workspace.show", {"name": name}
                ),
            )

        @self.tree.command(
            name="session-create", description="Create an Ethos session"
        )
        async def session_create(
            interaction: discord.Interaction, workspace: str
        ) -> None:
            responses = await self._respond(
                interaction,
                self._interaction_request(
                    interaction, "session.create", {"workspace": workspace}
                ),
            )
            if responses and interaction.channel_id is not None:
                session = responses[-1].data["session"]
                if isinstance(session, dict):
                    session_id = session.get("id")
                    if isinstance(session_id, str):
                        self._bindings[interaction.channel_id] = (
                            workspace,
                            session_id,
                        )

        @self.tree.command(
            name="session-list", description="List workspace sessions"
        )
        async def session_list(
            interaction: discord.Interaction, workspace: str
        ) -> None:
            await self._respond(
                interaction,
                self._interaction_request(
                    interaction, "session.list", {"workspace": workspace}
                ),
            )

        @self.tree.command(
            name="session-show", description="Show an Ethos session"
        )
        async def session_show(
            interaction: discord.Interaction,
            workspace: str,
            session_id: str,
        ) -> None:
            await self._respond(
                interaction,
                self._interaction_request(
                    interaction,
                    "session.show",
                    {"workspace": workspace, "session_id": session_id},
                ),
            )

        @self.tree.command(
            name="session-archive", description="Archive an Ethos session"
        )
        async def session_archive(
            interaction: discord.Interaction,
            workspace: str,
            session_id: str,
        ) -> None:
            responses = await self._respond(
                interaction,
                self._interaction_request(
                    interaction,
                    "session.archive",
                    {"workspace": workspace, "session_id": session_id},
                ),
            )
            if responses and interaction.channel_id is not None:
                bound = self._bindings.get(interaction.channel_id)
                if bound == (workspace, session_id):
                    self._bindings.pop(interaction.channel_id)

        @self.tree.command(name="chat", description="Chat with Ethos")
        async def chat(
            interaction: discord.Interaction,
            workspace: str,
            session_id: str,
            prompt: str,
        ) -> None:
            responses = await self._respond(
                interaction,
                self._interaction_request(
                    interaction,
                    "session.chat",
                    {
                        "workspace": workspace,
                        "session_id": session_id,
                        "prompt": prompt,
                    },
                ),
            )
            if responses and interaction.channel_id is not None:
                self._bindings[interaction.channel_id] = (
                    workspace,
                    session_id,
                )

    async def _new_default_session(self, message: discord.Message) -> str:
        responses = await self._execute(
            self._message_request(
                message,
                "session.create",
                {"workspace": DEFAULT_WORKSPACE},
            )
        )
        session = responses[-1].data["session"]
        if not isinstance(session, dict):
            raise RuntimeError("session.create returned an invalid session")
        session_id = session.get("id")
        if not isinstance(session_id, str):
            raise RuntimeError("session.create returned an invalid session")
        return session_id

    def _message_request(
        self,
        message: discord.Message,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> CommandRequest:
        return self._request(
            name,
            arguments,
            owner_id=str(message.author.id),
            channel_id=message.channel.id,
            guild_id=message.guild.id if message.guild is not None else None,
            message_id=message.id,
        )

    async def on_message(self, message: discord.Message) -> None:
        if (
            message.author.bot
            or message.author.id not in self._allowed_user_ids
            or not message.content.strip()
        ):
            return
        if message.guild is not None and self.user not in message.mentions:
            return

        prompt = message.content
        if self.user is not None:
            prompt = prompt.replace(f"<@{self.user.id}>", "")
            prompt = prompt.replace(f"<@!{self.user.id}>", "").strip()
        if not prompt:
            return

        async with self._channel_locks[message.channel.id]:
            binding = self._bindings.get(message.channel.id)
            if binding is None:
                session_id = await self._new_default_session(message)
                binding = (DEFAULT_WORKSPACE, session_id)
                self._bindings[message.channel.id] = binding

            workspace, session_id = binding
            try:
                text = ""
                async for response in self._execute_command(
                    self._message_request(
                        message,
                        "session.chat",
                        {
                            "workspace": workspace,
                            "session_id": session_id,
                            "prompt": prompt,
                        },
                    )
                ):
                    text += response.text
            except (FileNotFoundError, ValueError) as error:
                self._bindings.pop(message.channel.id, None)
                text = str(error)
            for chunk in _chunks(text):
                await message.channel.send(chunk)


class DiscordGateway(Gateway):
    """Expose Ethos commands through Discord."""

    def __init__(self, config: DiscordConfig) -> None:
        if config.token is None:
            raise ValueError("discord requires a bot token")
        self.config = config
        self._token = config.token.get_secret_value()

    @property
    def name(self) -> str:
        return _SOURCE

    def create_client(self, execute: CommandExecutor) -> _DiscordClient:
        """Create the configured Discord client."""
        return _DiscordClient(execute, self.config.allowed_user_ids)

    async def run(self, execute: CommandExecutor) -> None:
        """Connect to Discord until stopped or cancelled."""
        client = self.create_client(execute)
        async with client:
            await client.start(self._token)
