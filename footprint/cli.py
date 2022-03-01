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


@cli.command()
@click.option("-p", "--with-python", is_flag=True)
@click.option("-c", "--compile", is_flag=True)
@click.argument("project_dir", required=False)
def poetry_to_reqs(project_dir: str, with_python: bool, compile=True):
    """Generate a requirements file from pyproject.toml"""
    import os
    from io import StringIO

    import toml
    from invoke import Context

    pyproject = "pyproject.toml"
    if project_dir:
        pyproject = os.path.join(project_dir, pyproject)
    if not os.path.isfile(pyproject):
        raise click.BadArgumentUsage("no pyproject.toml file!")

    def fix(req):
        if req.startswith("^"):
            return f">={req[1:]}"
        return req

    reqs = "\n".join(
        f"{k}{fix(v)}"
        for k, v in sorted(
            toml.load(pyproject)["tool"]["poetry"]["dependencies"].items()
        )
        if with_python or k != "python"
    )
    if compile:
        Context().run("pip-compile", in_stream=StringIO(reqs), pty=True)
    else:
        click.echo(reqs)
