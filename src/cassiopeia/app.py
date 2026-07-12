import click

from cassiopeia.config import HOME_PATH
from cassiopeia.home import initialise_home


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


@main.command(hidden=True, add_help_option=False)
def debug() -> None:
    """development manual debug command, end users should not be seeing this"""
    click.echo("DEBUG")


if __name__ == "__main__":
    main()
