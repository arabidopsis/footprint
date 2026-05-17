from __future__ import annotations

import subprocess

import click

from .cli import cli
from .utils import which


def restart_userd() -> list[tuple[str, int]]:
    """Restart any user systemd files"""

    from .utils import userdir as u

    userdir = u()

    status: list[tuple[str, int]] = []

    systemctl = which("systemctl")

    for f in userdir.iterdir():
        if f.is_dir():  # skip directories
            continue
        if "@" in f.name:
            continue
        r = subprocess.run(
            [systemctl, "--user", "status", f.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # 4 unknown, 3 dead?
        if r.returncode == 3:
            # rep = r.stdout.strip()
            r = subprocess.run(
                [systemctl, "--user", "start", f.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            status.append((f.name, r.returncode))
        elif r.returncode != 0:
            status.append((f.name, r.returncode))

    return status


@cli.command()
def systemd_restart() -> None:
    """Restart any dead *user* systemd services"""
    from datetime import datetime

    restarted = restart_userd()
    col = {0: "green", 2: "yellow", 1: "yellow"}
    click.secho(f"at: {datetime.now()}")
    for service, code in restarted:
        s = click.style(service, bold=True, fg=col.get(code, "red"))
        click.echo(f"restart[{code}]: {s}")
    if any(ok != 0 for _, ok in restarted):
        raise click.Abort()
