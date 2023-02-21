from __future__ import annotations

from typing import Sequence
from typing import TextIO

import click

from .cli import cli


@cli.group(help=click.style("""Typescript related commands""", fg="magenta"))
def typescript():
    pass


@typescript.command(name="install")
@click.option(
    "-d",
    "--dir",
    "directory",
    metavar="DIRECTORY",
    default=".",
    help="directory to install typescript [default: current directory]",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.option("-y", "--yes", is_flag=True, help="Answer yes to all questions")
@click.argument("packages", nargs=-1)
def typescript_install(packages: Sequence[str], directory: str, yes: bool) -> None:
    """Install typescript in current directory

    Installs jquery and toastr types by default.
    """
    from shutil import which

    from invoke import Context

    pgks = set(packages)
    pgks.update(["jquery", "toastr"])
    c = Context()
    run = c.run
    y = "-y" if yes else ""
    err = lambda msg: click.secho(msg, fg="red", bold=True, err=True)
    npm = which("npm")
    if npm is None:
        err("No npm in PATH!")
        raise click.Abort()

    with c.cd(directory):
        run(f"{npm} init {y}", pty=True)  # create package.json
        run(f"{npm} install --save-dev typescript")
        for package in pgks:
            r = run(f"{npm} install --save-dev @types/{package}", pty=True, warn=True)
            if r.failed:
                err(f"failed to install {package}")
        run("npx tsc --init", pty=True)  # create tsconfig.json


@typescript.command(name="types")
@click.option("-r", "--raise", "raise_exc", is_flag=True, help="raise any exceptions")
@click.option("-l", "--lazy", is_flag=True, help="lazily evaluate unknown classes")
@click.option(
    "-e",
    "--no-errors",
    is_flag=True,
    help="don't put errors into output file as comments",
)
@click.option("-o", "--output", help="output file", type=click.File("wt"))
@click.argument("modules", nargs=-1)
def typescript_cmd(
    modules: list[str],
    no_errors: bool,
    raise_exc: bool,
    output: TextIO | None,
    lazy: bool = False,
) -> None:
    """Generate typescript from functions and dataclasses"""
    import sys

    from .typing import typescript as ts

    if "." not in sys.path:
        sys.path.append(".")
    ts(modules, no_errors=no_errors, raise_exc=raise_exc, output=output, lazy=lazy)
