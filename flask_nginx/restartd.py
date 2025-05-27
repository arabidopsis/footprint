from __future__ import annotations

import subprocess

import click

from .cli import cli
from .utils import which


def restart_userd() -> list[tuple[str, int]]:
    """Restart any user systemd files"""
    import os
    from os.path import isdir, join

    from .utils import userdir as u

    userdir = u()

    status: list[tuple[str, int]] = []

    systemctl = which("systemctl")

    for f in os.listdir(userdir):
        if isdir(join(userdir, f)):  # skip directories
            continue
        if "@" in f:
            continue
        r = subprocess.run(
            [systemctl, "--user", "status", f],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # 4 unknown, 3 dead?
        if r.returncode == 3:
            # rep = r.stdout.strip()
            r = subprocess.run(
                [systemctl, "--user", "start", f],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            status.append((f, r.returncode))
        elif r.returncode != 0:
            status.append((f, r.returncode))

    return status


@cli.command()
def systemd_restart() -> None:
    """Restart any dead *user* systemd services"""
    restarted = restart_userd()
    col = {0: "green", 2: "yellow", 1: "yellow"}
    for service, code in restarted:
        s = click.style(service, bold=True, fg=col.get(code, "red"))
        click.echo(f"restart[{code}]: {s}")
    if any(ok != 0 for _, ok in restarted):
        raise click.Abort()
