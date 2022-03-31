from __future__ import annotations

import click

from .cli import cli


def restart_userd() -> list[tuple[str, bool]]:
    """Restart any user systemd files"""
    import os
    from os.path import isdir, join

    from invoke import Context

    from .utils import userdir as u

    userdir = u()

    c = Context()
    status = []

    for f in os.listdir(userdir):
        if isdir(join(userdir, f)):  # skip directories
            continue
        r = c.run(f"systemctl --user status {f}", warn=True, hide=True)
        # 4 unknown, 3 dead?
        if r.exited == 3:
            # rep = r.stdout.strip()
            r = c.run(f"systemctl --user start {f}", warn=True, hide=True)

            status.append((f, r.exited))
        elif r.exited != 0:
            status.append((f, r.exited))

    return status


@cli.command()
def systemd_restart():
    """Restart any dead *user* systemd services"""
    restarted = restart_userd()
    col = {0: "green", 2: "yellow", 1: "yellow"}
    for service, code in restarted:
        s = click.style(service, bold=True, fg=col.get(code, "red"))
        click.echo(f"restart[{code}]: {s}")
    if any(ok != 0 for _, ok in restarted):
        raise click.Abort()
