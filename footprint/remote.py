import click

from .cli import cli
from .utils import get_pass, suresponder


def mount_irds(c, path, user, sudo=None):
    from .config import DATASTORE

    c.run(f"test -d '{path}' || mkdir -p '{path}'")
    if c.run(f"test -d '{path}/datastore'", warn=True).failed:
        pheme = get_pass("PHEME_PASSWORD", f"user {user} pheme")
        if sudo is None:
            sudo = suresponder(c)
        sudo(f"mount -t cifs -o user={user} -o pass={pheme} " f"{DATASTORE} {path}")
        if c.run(f"test -d {path}/datastore", warn=True).failed:
            raise RuntimeError("failed to mount IRDS datastore")

        def umount():
            sudo(f"umount {path}")

        return umount
    return None


def unmount_irds(machine, directory, sudo=None):
    from fabric import Connection

    with Connection(machine) as c:
        if not c.run(f"test -d '{directory}/datastore'", warn=True).failed:
            if sudo is None:
                sudo = suresponder(c)
            sudo(f"umount '{directory}'")
            return True
        return False


@cli.group(help=click.style("IRDS commands", fg="magenta"))
def irds():
    pass


@irds.command(name="mount")
@click.option("--user", help="user on remote machine")
@click.argument("src")
def mount_irds_(src, user):
    """Mount IRDS datastore."""
    from fabric import Connection

    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)

    with Connection(machine) as c:
        if not user:
            user = c.run("echo $USER", warn=True).stdout.strip()
        mount_irds(c, directory, user)


@irds.command(name="unmount")
@click.option(
    "--user", default="ianc", help="user on remote machine", show_default=True
)
@click.argument("src")
def unmount_irds_(src, user):
    """Unmount IRDS datastore."""
    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)
    if unmount_irds(machine, directory):
        click.secho("directory unmounted", fg="magenta")


@cli.command()
@click.option("-r", "--repo", default=".", help="repository location on local machine")
@click.option("-d", "--directory", default=".", help="location on remote machine")
@click.argument("machine")
def install_repo(machine, repo, directory):
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


@cli.command()
@click.argument("src")
def du(src):
    """find directory size."""
    from fabric import Connection

    from .utils import human

    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)
    with Connection(machine) as c:
        size, _ = c.run(f'du -sb "{directory}"', hide=True).stdout.strip().split()
        size = int(size)
    click.secho(f"{directory}: {human(size)}")
