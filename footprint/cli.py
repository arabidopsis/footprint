import click
from click_didyoumean import DYMGroup


@click.group(cls=DYMGroup, epilog=click.style("Footprint commands\n", fg="magenta"))
@click.version_option("0.1a")
def cli():
    pass
