import shutil
from collections.abc import Callable
from functools import wraps

import click

from cassiopeia.config import HOME_PATH
from cassiopeia.home import initialise_home
from cassiopeia.runtime import run_prompt


def requires_home[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """Require an initialised cassiopeia home for a command."""

    @wraps(command)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        if not HOME_PATH.is_dir():
            raise click.ClickException(
                "cassiopeia is not initialised. Run [cass init] first."
            )
        return command(*args, **kwargs)

    return guarded


@click.group()
def main() -> None:
    """cassieopeia agent harnesss"""


@main.command()
@click.option(
    "-r",
    "--reinitialise",
    is_flag=True,
    help="reinitialise a fresh app dir",
)
def init(reinitialise: bool) -> None:
    """(re)initialise cassiopeia app directory at ~/"""

    try:
        if reinitialise:
            if click.confirm(
                "Are you sure you want to reinitialise cassiopeia?\n"
                f"This will permanently delete {HOME_PATH}"
            ):
                initialise_home(HOME_PATH, reinitialise=True)
                click.echo(f".cassiopeia initialised at: {HOME_PATH}")
            else:
                click.echo("Aborted!")
            return

        initialise_home(HOME_PATH)
        click.echo(f".cassiopeia initialised at: {HOME_PATH}")
    except FileExistsError as error:
        raise click.ClickException(
            f"{error}.\nRun [cass init --reinitialise] to replace it."
        ) from error


@main.command()
def uninit() -> None:
    """Remove the cassiopeia app directory."""
    if not HOME_PATH.is_dir():
        raise click.ClickException(
            f"cassiopeia home does not exist at: {HOME_PATH}"
        )

    if click.confirm(
        "Are you sure you want to uninitialise cassiopeia?\n"
        f"This will permanently delete {HOME_PATH}"
    ):
        shutil.rmtree(HOME_PATH)
        click.echo(f".cassiopeia removed from: {HOME_PATH}")
    else:
        click.echo("Aborted!")


@main.command()
@click.argument("prompt")
@requires_home
def ask(prompt: str) -> None:
    """Send one prompt to the configured model."""
    try:
        click.echo(run_prompt(prompt))
    except Exception as error:
        raise click.ClickException(str(error)) from error


@main.command(hidden=True, add_help_option=False)
def debug() -> None:
    """development manual debug command, end users should not be seeing this"""
    click.echo("DEBUG")


if __name__ == "__main__":
    main()
