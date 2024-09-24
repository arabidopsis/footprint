from __future__ import annotations

import subprocess

import click

from .cli import cli
from .utils import which


def rsync(src: str, tgt: str, verbose: bool = False) -> None:
    rsync_cmd = which("rsync")

    v = ["-v"] if verbose else []

    if not src.endswith("/"):
        src += "/"
    if tgt.endswith("/"):
        tgt = tgt[:-1]

    cmd = [rsync_cmd, "-a"] + v + ["--delete", src, tgt]
    subprocess.run(cmd, check=True)


@cli.command(name="rsync")
@click.option("-v", "--verbose", is_flag=True)
@click.argument("src")
@click.argument("tgt")
def rsync_cmd(src: str, tgt: str, verbose: bool) -> None:
    """Sync two directories on two possibly different machines.

    e.g.: footprint rsync my/folder chloe:/var/www/folder
    """
    rsync(src, tgt, verbose)
