import click

@click.group()
def main() -> None:
    """Cassiopeia command line interface."""


@main.command()
def run(p) -> None:
    click.echo("hello world")