import click
from click_didyoumean import DYMGroup


@click.group(cls=DYMGroup, epilog=click.style("Footprint commands\n", fg="magenta"))
def cli():
    pass
