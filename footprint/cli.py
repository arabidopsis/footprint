from __future__ import annotations

import subprocess

import click

from .config import VERSION

# from click_didyoumean import DYMGroup


@click.group(
    # cls=DYMGroup,
    epilog=click.style("Commands to manage websites\n", fg="magenta"),
)
@click.version_option(VERSION)
def cli() -> None:
    pass


@cli.command()
def update() -> None:
    """Update this package"""
    import sys

    from .config import REPO

    ret = subprocess.call([sys.executable, "-m", "pip", "install", "-U", REPO])
    if ret:
        click.secho(f"can't install {REPO}", fg="red")
        raise click.Abort()


@cli.command()
def show_config() -> None:
    """Show configuration"""
    from dataclasses import fields
    from .config import get_config

    Config = get_config()

    n = max(len(f.name) for f in fields(Config))

    for f in fields(Config):
        k = f.name
        v = getattr(Config, f.name)
        print(f"{k:<{n}}: {v}")


# @cli.command()
# @click.option("-p", "--with-python", is_flag=True)
# @click.option("-c", "--compile", "use_pip_compile", is_flag=True)
# @click.argument("project_dir", required=False)
def poetry_to_reqs(
    project_dir: str,
    with_python: bool,
    use_pip_compile: bool = True,
) -> None:
    """Generate a requirements.txt file from pyproject.toml [**may require toml**]"""
    import os
    from contextlib import suppress
    from .utils import toml_load

    pyproject = "pyproject.toml"
    if project_dir:
        pyproject = os.path.join(project_dir, pyproject)
    if not os.path.isfile(pyproject):
        raise click.BadArgumentUsage("no pyproject.toml file!")

    def fix(req: str) -> str:
        if req.startswith("^"):
            return f">={req[1:]}"
        return req

    reqs = "\n".join(
        f"{k}{fix(v)}"
        for k, v in sorted(
            toml_load(pyproject)["tool"]["poetry"]["dependencies"].items(),
        )
        if with_python or k != "python" and isinstance(v, str)
    )
    if use_pip_compile:
        try:
            with open("requirements.in", "w", encoding="utf-8") as fp:
                click.echo(reqs, file=fp)
            subprocess.check_call(["pip-compile"])
        finally:
            with suppress(OSError):
                os.remove("requirements.in")
    else:
        click.echo(reqs)
