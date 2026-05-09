from __future__ import annotations

import subprocess
from getpass import getuser

import click

from .cli import cli
from .systemd.systemd import systemd
from .systemd.utils import make_args
from .utils import get_pass
from .utils import which


def mount_irds(
    datastore: str,
    path_str: str,
    user: str | None = None,
    credentials: str | None = None,
) -> int:
    from pathlib import Path
    import os

    sudo = which("sudo")
    mount = which("mount")

    path = Path(path_str).expanduser().absolute()
    if not path.exists():
        path.mkdir(exist_ok=True, parents=True)

    # datastore = path / "datastore"
    # if datastore.exists():
    #     return 0
    args = []
    if credentials is not None:
        c = str(Path(credentials).expanduser().absolute())
        args.append(f"credentials={c}")
    else:
        if user is None:
            user = getuser()
        args.append(f"user={user}")
        pheme = get_pass("PHEME", f"user {user} pheme")
        args.append(f"password={pheme}")

    uid = os.getuid()
    gid = os.getgid()
    a = ",".join(args)
    cmd = [
        sudo,
        mount,
        "-t",
        "cifs",
        "-o",
        f"uid={uid},gid={gid},forceuid,forcegid,{a}",
        datastore,
        str(path),
    ]
    pmount = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    returncode = pmount.wait()
    return returncode


@cli.group(help=click.style("IRDS commands", fg="magenta"))
def irds() -> None:
    pass


@irds.command(name="mount")
@click.option(
    "-c",
    "--credentials",
    type=click.Path(file_okay=True, dir_okay=False, exists=True),
)
@click.argument("datastore")
@click.argument("directory")
@click.argument("user", required=False)
def mount_irds_cmd(
    datastore: str,
    directory: str,
    credentials: str | None,
    user: str | None,
) -> None:
    """Mount IRDS datastore."""

    returncode = mount_irds(datastore, directory, user=user, credentials=credentials)
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
    "uid": "user id",
    "gid": "group id",
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
@click.option(
    "-c",
    "--credentials",
    type=click.Path(file_okay=True, dir_okay=False, exists=True),
    help="credentials file for CIFS",
)
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@click.option("-n", "--no-check", is_flag=True, help="don't check parameters")
@click.argument(
    "datastore",
    required=True,
)
@click.argument(
    "mount_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=True,
)
@click.argument("params", nargs=-1)
def systemd_mount_cmd(
    datastore: str,  # e.g. "//drive.irds.uwa.edu.au/sci-ms-001"
    mount_dir: str,
    params: list[str],
    template: str | None,
    no_check: bool,
    ignore_unknowns: bool,
    credentials: str | None,
) -> None:
    """Generate a systemd unit file to mount IRDS.

    PARAMS are key=value arguments for the template.
    """
    import os
    from getpass import getpass

    params = list(params)

    mount_dir = mount_dir or "."
    mount_dir = os.path.abspath(os.path.expanduser(mount_dir))

    def isadir(d: str) -> str | None:
        return None if os.path.isdir(d) else f"{d}: not a directory"

    def isafile(d: str) -> str | None:
        return None if os.path.isfile(d) else f"{d}: not a file"

    se = which("systemd-escape")
    filename = subprocess.check_output(
        [se, "-p", "--suffix=mount", mount_dir],
        text=True,
    ).strip()

    if credentials is not None:
        params.append(f"credentials={os.path.expanduser(credentials)}")

    systemd(
        template or "systemd.mount",
        mount_dir,
        params,
        help_args={**MOUNT_ARGS, "port": "unused"},
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
            ("drive", lambda _: datastore),
            (
                "password",
                lambda params: (
                    getpass(f"PHEME password for {params['user']}: ")
                    if "credentials" not in params
                    else None
                ),
            ),
        ],
    )
    msg = click.style(f"footprint config systemd-install {filename}", fg="green")
    click.echo(f'use: "{msg}" to install')
