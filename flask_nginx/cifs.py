from __future__ import annotations

import subprocess
from getpass import getuser
from pathlib import Path

import click

from .cli import cli
from .systemd.systemd import systemd
from .systemd.utils import ignore_unknowns_option, make_args
from .utils import get_pass, which


def mount_cifs(
    datastore: str,
    path_str: str,
    user: str | None = None,
    credentials: str | None = None,
) -> int:
    import os
    from pathlib import Path

    sudo = which("sudo")
    mount = which("mount")

    path = Path(path_str).expanduser().absolute()
    if not path.exists():
        path.mkdir(exist_ok=True, parents=True)

    args = []
    if credentials is not None:
        c = str(Path(credentials).expanduser().absolute())
        args.append(f"credentials={c}")
    else:
        if user is None:
            user = getuser()
        args.append(f"user={user}")
        password = get_pass("CIFS", f"user {user} password")
        args.append(f"password={password}")

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
    return pmount.wait()


@cli.group(help=click.style("CIFS commands", fg="magenta"))
def cifs() -> None:
    pass


@cifs.command(name="mount")
@click.option(
    "-c",
    "--credentials",
    type=click.Path(file_okay=True, dir_okay=False, exists=True),
)
@click.argument("datastore", required=True)
@click.argument("mount_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False), required=True)
@click.argument("user", required=False)
def mount_cifs_cmd(
    datastore: str,
    mount_dir: str,
    credentials: str | None,
    user: str | None,
) -> None:
    """Mount CIFS datastore."""
    returncode = mount_cifs(datastore, mount_dir, user=user, credentials=credentials)
    if returncode != 0:
        click.secho("can't mount cifs", fg="red")
        raise click.Abort


MOUNT_ARGS = {
    "user": "user to run as [default: current user]",
    "version": "SMB version [default: 3.0]",
    "password": "CIFS password",
    "uid": "user id for mount ownership [default: current user id]",
    "gid": "group id for mount ownership [default: current user gid]",
    "timeoutsec": "timeout in seconds [default: 30]",
}

MOUNT_HELP = f"""
Generate a systemd mount file for a CIFS filesystem.

Use footprint cifs systemd path/to/mount_dir ... etc.
with the following arguments:

\b
{make_args(MOUNT_ARGS)}
\b
example:
\b
footprint cifs systemd -c /path/to/credentials //drive.irds.uwa.edu.au/lab-group-001 /path/to/dir
"""


@cifs.command(name="systemd", help=MOUNT_HELP)
@ignore_unknowns_option
@click.option(
    "-c",
    "--credentials",
    type=click.Path(file_okay=True, dir_okay=False, exists=True, path_type=Path),
    help="credentials file for CIFS access",
)
@click.option(
    "-t",
    "--template",
    metavar="TEMPLATE_FILE",
    help="template file",
    type=click.Path(file_okay=True, dir_okay=False, exists=True, path_type=Path),
)
@click.option("-n", "--no-check", is_flag=True, help="don't check parameters")
@click.argument(
    "datastore",
    required=True,
)
@click.argument(
    "mount_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False, path_type=Path),
    required=True,
)
@click.argument("params", nargs=-1)
def systemd_mount_cmd(
    datastore: str,  # e.g. "//drive.irds.uwa.edu.au/sci-ms-001"
    mount_dir: Path | None,
    params: list[str],
    template: Path | None,
    credentials: Path | None,
    *,
    no_check: bool,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to mount IRDS.

    PARAMS are key=value arguments for the template.
    """
    import pwd
    from getpass import getpass

    params = list(params)

    mount_dir = mount_dir or Path.cwd()
    mount_dir = mount_dir.expanduser().resolve()

    def isafile(d: str) -> str | None:
        return None if Path(d).is_file() else f"{d}: not a file"

    se = which("systemd-escape")
    filename = subprocess.check_output(
        [se, "-p", "--suffix=mount", mount_dir],
        text=True,
    ).strip()

    if credentials is not None:
        params.append(f"credentials={credentials.expanduser().absolute()!s}")
    ex = {
        "drive": "CIFS drive to mount",
        "credentials": "file containing CIFS password",
    }

    systemd(
        template or "systemd.mount",
        mount_dir,
        params,
        help_args={**MOUNT_ARGS, **ex},
        check=not no_check,
        output=filename,
        ignore_unknowns=ignore_unknowns,
        checks=[
            ("credentials", lambda _, v: isafile(v)),
        ],
        default_values=[
            ("user", lambda _: getuser()),
            ("uid", lambda params: str(pwd.getpwnam(params["user"]).pw_uid)),
            ("gid", lambda params: str(pwd.getpwnam(params["user"]).pw_gid)),
            ("drive", lambda _: datastore),
            (
                "password",
                lambda params: (
                    getpass(f"CIFS password for {params['user']}: ") if "credentials" not in params else None
                ),
            ),
        ],
    )
    msg = click.style(f"footprint config systemd-install {filename}", fg="green")
    click.echo(f'use: "{msg}" to install')
