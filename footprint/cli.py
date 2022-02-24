import click
from click_didyoumean import DYMGroup
from flask import request_finished

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


@cli.command()
@click.argument("project_dir", required=False)
def poetry_to_reqs(project_dir):
    """Generate a requirements file from pyproject.toml"""
    import os
    import toml

    pyproject = "pyproject.toml"
    if project_dir:
        pyproject = os.path.join(project_dir, pyproject)
    if not os.path.isfile(pyproject):
        raise click.BadArgumentUsage("no pyproject.toml file!")

    reqs = "\n".join(
        f"{k}{v}"
        for k, v in toml.load(pyproject)["tool"]["poetry"]["dependencies"].items()
    )
    click.echo(reqs)
