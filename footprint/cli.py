import click
from click_didyoumean import DYMGroup

from .config import VERSION


@click.group(cls=DYMGroup, epilog=click.style("Footprint commands\n", fg="magenta"))
@click.version_option(VERSION)
def cli():
    pass


@cli.command()
def update():
    """Update this package"""
    import sys
    from invoke import Context
    from .config import REPO

    cmd = f"{sys.executable} -m pip install -U '{REPO}'"
    Context().run(cmd)
