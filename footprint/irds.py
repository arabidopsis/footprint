import typing as t

import click
from invoke import Context

from .cli import cli
from .systemd import config_options, make_args, systemd
from .utils import SUDO, get_pass, get_sudo, make_connection


def mount_irds(
    c: Context,
    path: str,
    user: str,
    sudo: t.Optional[SUDO] = None,
    use_su: bool = False,
    verbose: bool = False,
) -> t.Optional[t.Callable[[], None]]:
    from .config import DATASTORE

    c.run(f"test -d '{path}' || mkdir -p '{path}'")
    if c.run(f"test -d '{path}/datastore'", warn=True).failed:
        pheme = get_pass("PHEME", f"user {user} pheme")
        if sudo is None:
            sudo = get_sudo(c, use_su)
        uid = c.run("id -u", hide=True).stdout.strip()
        gid = c.run("id -g", hide=True).stdout.strip()
        cmd = (
            f"mount -t cifs -o user={user} -o pass='{pheme}' -o uid={uid},gid={gid},forceuid,forcegid "
            f"{DATASTORE} {path}"
        )
        if verbose:
            click.echo(cmd)
        sudo(cmd)

        if c.run(f"test -d {path}/datastore", warn=True).failed:
            raise RuntimeError("failed to mount IRDS datastore")

        def umount():
            sudo(f"umount {path}")

        return umount
    return None


def unmount_irds(
    machine: t.Optional[str],
    directory: str,
    sudo: t.Optional[SUDO] = None,
    use_su: bool = False,
) -> bool:
    with make_connection(machine) as c:
        if not c.run(f"test -d '{directory}/datastore'", warn=True).failed:
            if sudo is None:
                sudo = get_sudo(c, use_su)
            sudo(f"umount '{directory}'")
            return True
        return False


@cli.group(help=click.style("IRDS commands", fg="magenta"))
def irds():
    pass


@irds.command(name="mount")
@click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")
@click.option("-U", "--user", help="user on remote machine")
@click.option("-v", "--verbose", is_flag=True, help="show command")
@click.argument("directory")
@click.argument("machine", required=False)
def mount_irds_(
    directory: str,
    machine: t.Optional[str],
    use_su: bool,
    user: t.Optional[str],
    verbose: bool,
) -> None:
    """Mount IRDS datastore."""

    def get_user(c) -> str:
        user = c.run("echo $USER", warn=True, hide=True).stdout.strip()
        if not user:
            raise click.BadParameter("can't find user", param_hint="user")
        return user

    with make_connection(machine) as c:
        if not user:
            user = get_user(c)
        mount_irds(c, directory, user, use_su=use_su, verbose=verbose)


@irds.command(name="unmount")
@click.option(
    "--user", default="ianc", help="user on remote machine", show_default=True
)
@click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")
@click.argument("directory")
@click.argument("machine", required=False)
def unmount_irds_(
    machine: t.Optional[str], directory: str, use_su: bool, user: t.Optional[str]
) -> None:
    """Unmount IRDS datastore."""

    if unmount_irds(machine, directory, None, use_su):
        click.secho("directory unmounted", fg="magenta")


MOUNT_ARGS = {
    "mount_dir": "locations of repo",
    "user ": "user to run as [default: current user]",
    "version": "SMB version [default: 3.0]",
    "credentials": "file containg PHEME password",
    "password": "PHEME password",
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
    footprint irds systemd ~/irds user=00000
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
    mount_dir: t.Optional[str],
    params: t.List[str],
    template: t.Optional[str],
    no_check: bool,
    output: t.Optional[str],
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to mount IRDS.

    PARAMS are key=value arguments for the template.
    """
    import os
    from getpass import getpass

    mount_dir = mount_dir or "."
    mount_dir = os.path.abspath(os.path.expanduser(mount_dir))

    def isadir(d):
        return None if os.path.isdir(d) else f"{d}: not a directory"

    def isafile(d):
        return None if os.path.isfile(d) else f"{d}: not a file"

    c = Context()
    output = c.run(
        f'systemd-escape -p --suffix=mount "{mount_dir}"', hide=True
    ).stdout.strip()
    systemd(
        template or "systemd.mount",
        mount_dir,
        params,
        help_args=MOUNT_ARGS,
        check=not no_check,
        output=output,
        ignore_unknowns=ignore_unknowns,
        checks=[
            (
                "mount_dir",
                lambda _, v: isadir(v),
            ),
            (
                "credentials",
                lambda _, v: isafile(v),
            ),
        ],
        default_values=[
            ("uid", lambda _: c.run("id -u", hide=True).stdout.strip()),
            ("gid", lambda _: c.run("id -g", hide=True).stdout.strip()),
            (
                "password",
                lambda params: getpass(f"PHEME password for {params['user']}: ")
                if "credentials" not in params
                else None,
            ),
        ],
    )
    click.secho(
        f'use: "footprint config systemd-install {output}" to install', fg="green"
    )
