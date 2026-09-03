from __future__ import annotations

import subprocess

import click

from .cli import cli
from .utils import which


def rsync(src: str, tgt: str, *, verbose: bool = False, delete: bool = False) -> None:
    rsync_cmd = which("rsync")

    v = ["-v"] if verbose else []

    if delete:
        v.append("--delete")

    if not src.endswith("/"):
        src += "/"
    tgt = tgt.removesuffix("/")

    cmd = [rsync_cmd, "-a", *v, src, tgt]
    subprocess.run(cmd, check=True)


@cli.command(name="rsync")
@click.option("-v", "--verbose", is_flag=True)
@click.option("--delete", is_flag=True, help="delete files in target that are not in source")
@click.argument("src")
@click.argument("tgt")
def rsync_cmd(src: str, tgt: str, *, verbose: bool, delete: bool = False) -> None:
    """Sync two directories on two possibly different machines.

    e.g.: footprint rsync my/folder chloe:/var/www/folder
    """
    rsync(src, tgt, verbose=verbose, delete=delete)
