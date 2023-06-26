from __future__ import annotations

import click

from .config import VERSION

# from click_didyoumean import DYMGroup


@click.group(
    # cls=DYMGroup,
    epilog=click.style("Commands to manage websites\n", fg="magenta"),
)
@click.version_option(VERSION)
def cli() -> None:
    from .utils import init_config

    init_config()


@cli.command()
def update() -> None:
    """Update this package"""
    import sys

    from invoke import Context

    from .config import REPO

    cmd = f"{sys.executable} -m pip install -U '{REPO}'"
    Context().run(cmd)


@cli.command()
def show_config() -> None:
    """Show configuration"""
    from . import config

    keys = sorted(k for k in dir(config) if k.isupper())
    n = len(max(keys, key=len))
    for k in keys:

        v = getattr(config, k)
        print(f"{k:<{n}}: {v}")


@cli.command()
@click.option("-p", "--with-python", is_flag=True)
@click.option("-c", "--compile", "use_pip_compile", is_flag=True)
@click.argument("project_dir", required=False)
def poetry_to_reqs(project_dir: str, with_python: bool, use_pip_compile=True) -> None:
    """Generate a requirements.txt file from pyproject.toml [**may require toml**]"""
    import os
    from contextlib import suppress
    from .utils import toml_load

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
            toml_load(pyproject)["tool"]["poetry"]["dependencies"].items(),
        )
        if with_python or k != "python"
    )
    if use_pip_compile:
        try:
            with open("requirements.in", "w", encoding="utf-8") as fp:
                click.echo(reqs, file=fp)
            Context().run("pip-compile", pty=True)
        finally:
            with suppress(OSError):
                os.remove("requirements.in")
    else:
        click.echo(reqs)
