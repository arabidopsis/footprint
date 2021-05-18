import os

import click
from fabric import Connection

from .cli import cli
from .utils import get_pass, suresponder


def mount_irds(c, path, user, sudo=None):
    from .config import DATASTORE

    c.run(f"test -d '{path}' || mkdir -p '{path}'")
    if c.run(f"test -d '{path}/datastore'", warn=True).failed:
        pheme = get_pass("PHEME_PASSWORD", f"user {user} pheme")
        if sudo is None:
            sudo = suresponder(c, rootpw=os.environ.get("ROOT_PASSWORD"))
        sudo(f"mount -t cifs -o user={user} -o pass={pheme} " f"{DATASTORE} {path}")
        if c.run(f"test -d {path}/datastore", warn=True).failed:
            raise RuntimeError("failed to mount IRDS datastore")

        def umount():
            sudo(f"umount {path}")

        return umount
    return None


def unmount_irds(machine, directory, sudo=None):
    with Connection(machine) as c:
        if not c.run(f"test -d '{directory}/datastore'", warn=True).failed:
            click.secho(f"unmounting {directory}", fg="magenta")
            if sudo is None:
                sudo = suresponder(c, rootpw=os.environ.get("ROOT_PASSWORD"))
            sudo(f"umount '{directory}'")


@cli.command(name="mount-irds")
@click.option(
    "--user", default="ianc", help="user on remote machine", show_default=True
)
@click.argument("src")
def mount_irds_(src, user):
    """Mount IRDS datastore."""
    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)

    with Connection(machine) as c:
        mount_irds(c, directory, user)


@cli.command(name="unmount-irds")
@click.option(
    "--user", default="ianc", help="user on remote machine", show_default=True
)
@click.argument("src")
def unmount_irds_(src, user):
    """Unmount IRDS datastore."""
    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)
    unmount_irds(machine, directory)


@cli.command()
@click.option("-r", "--repo", default=".", help="repository location on local machine")
@click.option("-d", "--directory", default=".", help="location on remote machine")
@click.argument("machine")
def install_repo(machine, repo, directory):
    """Install a repo on a remote machine."""

    with Connection(machine) as c:
        if directory != ".":
            c.run(f'mkdir -p "{directory}"')
        with c.cd(directory):
            r = c.local(
                f"git -C {repo} config --get remote.origin.url", warn=True, hide=True
            ).stdout.strip()
            c.run(f"git clone {r}", pty=True)


@click.argument("src")
def du(src):
    """find directory size."""
    from .utils import human

    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)
    with Connection(machine) as c:
        size, _ = c.run(f'du -sb "{directory}', hide=True).stdout.strip().split()
        size = int(size)
    click.secho(f"{directory}: {human(size)}")
