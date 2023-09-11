from __future__ import annotations

import subprocess
from getpass import getuser

import click

from .cli import cli
from .systemd import config_options
from .systemd import make_args
from .systemd import systemd
from .utils import get_pass
from .utils import which


def mount_irds(path: str, user: str | None = None) -> int:
    from .config import DATASTORE
    from pathlib import Path
    import os

    p = Path(path).expanduser()
    if not p.exists():
        p.mkdir(exist_ok=True, parents=True)

    datastore = p / "datastore"
    if datastore.exists():
        return 0

    if user is None:
        user = getuser()
    uid = os.getuid()
    gid = os.getgid()
    sudo = which("sudo")
    mount = which("mount")
    pheme = get_pass("PHEME", f"user {user} pheme")
    cmd = [
        sudo,
        mount,
        "-t",
        "cifs",
        "-o",
        f"user={user}",
        "-o",
        f"pass={pheme}",
        "-o",
        f"uid={uid},gid={gid},forceuid,forcegid",
        DATASTORE,
        str(p),
    ]
    pmount = subprocess.Popen(cmd)
    returncode = pmount.wait()
    return returncode


@cli.group(help=click.style("IRDS commands", fg="magenta"))
def irds() -> None:
    pass


@irds.command(name="mount")
@click.argument("directory")
@click.argument("user", required=False)
def mount_irds_(directory: str, user: str | None) -> None:
    """Mount IRDS datastore."""

    returncode = mount_irds(directory, user)
    if returncode != 0:
        click.secho("can't mound irds", fg="red")
        raise click.Abort()


MOUNT_ARGS = {
    "mount_dir": "locations of repo",
    "user": "user to run as [default: current user]",
    "version": "SMB version [default: 3.0]",
    "credentials": "file containg PHEME password as a line: password={pw}"
    " (no spaces)\nroot owned with permission 600",
    "password": "PHEME password",
    "drive": "IRDS drive to mount",
}

MOUNT_HELP = f"""
Generate a systemd mount file for a IRDS.

Use footprint irds systemd path/to/mount_dir ... etc.
with the following arguments:

\b
{make_args(MOUNT_ARGS)}
\b
example:
\b
footprint irds systemd ~/irds user=00033472
"""


@irds.command(name="systemd", help=MOUNT_HELP)
@click.option("-i", "--ignore-unknowns", is_flag=True, help="ignore unknown variables")
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@config_options
@click.argument(
    "mount_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1)
def systemd_mount(
    mount_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to mount IRDS.

    PARAMS are key=value arguments for the template.
    """
    import os
    from getpass import getpass

    from .config import DATASTORE

    mount_dir = mount_dir or "."
    mount_dir = os.path.abspath(os.path.expanduser(mount_dir))

    def isadir(d):
        return None if os.path.isdir(d) else f"{d}: not a directory"

    def isafile(d):
        return None if os.path.isfile(d) else f"{d}: not a file"

    se = which("systemd-escape")
    filename = subprocess.check_output(
        [se, "-p" "--suffix=mount", "mount_dir"],
        text=True,
    ).strip()

    systemd(
        template or "systemd.mount",
        mount_dir,
        params,
        help_args=MOUNT_ARGS,
        check=not no_check,
        output=filename,
        ignore_unknowns=ignore_unknowns,
        checks=[
            (
                "mount_dir",
                lambda _, v: isadir(v),
            ),
            ("credentials", lambda _, v: isafile(v)),
        ],
        default_values=[
            ("uid", lambda _: str(os.getuid())),
            ("gid", lambda _: str(os.getgid())),
            ("drive", lambda _: DATASTORE),
            (
                "password",
                lambda params: getpass(f"PHEME password for {params['user']}: ")
                if "credentials" not in params
                else None,
            ),
        ],
    )
    msg = click.style(f"footprint config systemd-install {output}", fg="green")
    click.echo(f'use: "{msg}" to install')
