"""Click entry point for cassiopeia administration commands."""

from pathlib import Path

import click

from cassiopeia.config import load_settings
from cassiopeia.home import initialise_home

PLACEHOLDER_GROUPS: tuple[str, ...] = (
    "session",
    "workspace",
    "persona",
    "skill",
    "workflow",
    "hook",
    "memory",
    "permission",
    "gateway",
    "storage",
)


class CassGroup(click.Group):
    """Top-level Click group that separates live commands from reserved groups."""

    def format_commands(
        self,
        ctx: click.Context,
        formatter: click.HelpFormatter,
    ) -> None:
        real_commands: list[tuple[str, click.Command]] = []
        reserved_commands: list[tuple[str, click.Command]] = []

        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)

            if command is None:
                continue

            if name in PLACEHOLDER_GROUPS:
                reserved_commands.append((name, command))
            else:
                real_commands.append((name, command))

        self._write_command_section(
            formatter,
            "Commands",
            real_commands,
        )

        self._write_command_section(
            formatter,
            "Reserved Commands",
            reserved_commands,
        )

    def _write_command_section(
        self,
        formatter: click.HelpFormatter,
        heading: str,
        commands: list[tuple[str, click.Command]],
    ) -> None:
        if not commands:
            return

        rows = []
        widest_name = max(len(name) for name, _ in commands)

        for name, command in commands:
            help_text = command.get_short_help_str(limit=formatter.width - 6 - widest_name)
            rows.append((name, help_text))

        with formatter.section(heading):
            formatter.write_dl(rows)


@click.group(cls=CassGroup)
def main() -> None:
    """Cassiopeia command line interface."""


def not_implemented_group(name: str) -> click.Group:
    """Build a reserved command group that fails clearly until implemented."""

    @click.group(name=name, invoke_without_command=True)
    @click.pass_context
    def command_group(context: click.Context) -> None:
        if context.invoked_subcommand is None:
            raise click.ClickException(
                f"`cass {name}` is reserved for future administration commands."
            )

    command_group.help = f"Reserved {name} administration commands."
    return command_group


for group_name in PLACEHOLDER_GROUPS:
    main.add_command(not_implemented_group(group_name))


@main.command()
@click.argument("message")
def ask(message: str) -> None:
    """TODO: implement post milestone 01, oneshot LLM ask."""
    if not message:
        click.echo("hello world")
    else:
        click.echo(f"hello world: {message}")


@main.command()
@click.option(
    "--home",
    "home",
    type=click.Path(path_type=Path),
)
def init(home: Path | None) -> None:
    """Initialise the cassiopeia home directory."""
    settings = load_settings()
    try:
        home_dir = initialise_home(home if home is not None else settings.home)
    except FileExistsError as error:
        raise click.ClickException(str(error)) from error

    click.echo(home_dir)
