from pathlib import Path

import click

from cassiopeia.config import load_settings
from cassiopeia.home import initialise_home


@click.group()
def main() -> None:
    """Cassiopeia command line interface."""


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
    envvar="CASSIOPEIA_HOME",
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
