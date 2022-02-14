import typing as t

import click
from invoke import Context

from .cli import cli
from .utils import SUDO, get_pass, sudoresponder, suresponder


def mount_irds(
    c: Context,
    path: str,
    user: str,
    sudo: t.Optional[SUDO] = None,
    use_su: bool = False,
) -> t.Optional[t.Callable[[], None]]:
    from .config import DATASTORE

    c.run(f"test -d '{path}' || mkdir -p '{path}'")
    if c.run(f"test -d '{path}/datastore'", warn=True).failed:
        pheme = get_pass("PHEME_PASSWORD", f"user {user} pheme")
        if sudo is None:
            sudo = suresponder(c) if use_su else sudoresponder(c)
        sudo(f"mount -t cifs -o user={user} -o pass={pheme} " f"{DATASTORE} {path}")
        if c.run(f"test -d {path}/datastore", warn=True).failed:
            raise RuntimeError("failed to mount IRDS datastore")

        def umount():
            sudo(f"umount {path}")

        return umount
    return None


def unmount_irds(
    machine: str, directory: str, sudo: t.Optional[SUDO] = None, use_su: bool = False
) -> bool:
    from fabric import Connection

    with Connection(machine) as c:
        if not c.run(f"test -d '{directory}/datastore'", warn=True).failed:
            if sudo is None:
                sudo = suresponder(c) if use_su else sudoresponder(c)
            sudo(f"umount '{directory}'")
            return True
        return False


@cli.group(help=click.style("IRDS commands", fg="magenta"))
def irds():
    pass


@irds.command(name="mount")
@click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")
@click.option("--user", help="user on remote machine")
@click.argument("machine")
@click.argument("directory")
def mount_irds_(
    machine: str, directory: str, use_su: bool, user: t.Optional[str]
) -> None:
    """Mount IRDS datastore."""
    from fabric import Connection

    with Connection(machine) as c:
        if not user:
            user = c.run("echo $USER", warn=True, hide=True).stdout.strip()
        if not user:
            raise click.BadParameter("can't find user", param_hint="user")
        mount_irds(c, directory, user, use_su=use_su)


@irds.command(name="unmount")
@click.option(
    "--user", default="ianc", help="user on remote machine", show_default=True
)
@click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")
@click.argument("machine")
@click.argument("directory")
def unmount_irds_(
    machine: str, directory: str, use_su: bool, user: t.Optional[str]
) -> None:
    """Unmount IRDS datastore."""

    if unmount_irds(machine, directory, None, use_su):
        click.secho("directory unmounted", fg="magenta")


@cli.command()
@click.option("-r", "--repo", default=".", help="repository location on local machine")
@click.option("-d", "--directory", default=".", help="location on remote machine")
@click.argument("machine")
def install_repo(machine: str, repo: str, directory: str) -> None:
    """Install a repo on a remote machine."""
    from fabric import Connection

    with Connection(machine) as c:
        if directory != ".":
            c.run(f'mkdir -p "{directory}"')
        with c.cd(directory):
            r = c.local(
                f"git -C {repo} config --get remote.origin.url", warn=True, hide=True
            ).stdout.strip()
            c.run(f"git clone {r}", pty=True)


@cli.command(
    epilog=click.style(
        """use e.g.: footprint secret >> instance/app.cfg""", fg="magenta"
    )
)
@click.option("--size", default=32, help="size of secret in bytes", show_default=True)
def secret(size: int):
    """Generate secret keys for Flask apps"""
    from secrets import token_bytes

    print("SECRET_KEY =", token_bytes(size))
    print("SECURITY_PASSWORD_SALT =", token_bytes(size))


@cli.command()
@click.option("--su", "asroot", is_flag=True, help="run as root")
@click.argument("src")
def du(src: str, asroot: bool) -> None:
    """find directory size."""
    from fabric import Connection

    from .utils import human

    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)
    with Connection(machine) as c:
        if asroot:
            run = suresponder(c)
        else:
            run = c.run
        o = run(f'du -sb "{directory}"', hide=True).stdout.strip()
        if asroot:
            o = o.replace("Password:", "").strip()
        size, _ = o.split()
        size = int(size)
    click.secho(f"{directory}: {human(size)}")
