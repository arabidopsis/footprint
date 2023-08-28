from __future__ import annotations

import subprocess
from shutil import which

import click

from .cli import cli


def rsync(src: str, tgt: str, verbose: bool = False) -> None:
    v = ["-v"] if verbose else []

    if not src.endswith("/"):
        src += "/"
    if tgt.endswith("/"):
        tgt = tgt[:-1]
    rsync = which("rsync")
    if rsync is None:
        raise RuntimeError("can't find rsync!")

    cmd = [rsync, "-a"] + v + ["--delete", src, tgt]
    subprocess.run(cmd, check=True)


@cli.command(name="rsync")
@click.option("-v", "--verbose", is_flag=True)
@click.argument("src")
@click.argument("tgt")
def rsync_(src: str, tgt: str, verbose: bool):
    """Sync two directories on two possibly different machines."""
    rsync(src, tgt, verbose)
