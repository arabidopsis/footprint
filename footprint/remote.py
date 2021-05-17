import getpass
import os
import re
from collections import namedtuple

import click

from .cli import cli

IP = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")


def get_pass(VAR, msg):
    if VAR not in os.environ:
        return getpass.getpass(f"{msg} password: ")
    return os.environ[VAR]


def suresponder(c, rootpw=None):
    from invoke import Responder

    if rootpw is None:
        rootpw = getpass.getpass(f"{c.host}: *root* password: ")
    supass = Responder(pattern="Password:", response=rootpw + "\n")

    def sudo(cmd, **kw):
        # https://www.gnu.org/software/bash/manual/html_node/Single-Quotes.html
        # cmd = cmd.replace("'", r"\'")
        cmd = cmd.replace('"', r"\"")
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(f'su -c "{cmd}"', watchers=[supass], **kw)

    return sudo


def mount_irds(c, path, user, sudo=None):
    c.run(f"test -d '{path}' || mkdir -p '{path}'")
    if c.run(f"test -d '{path}/datastore'", warn=True).failed:
        pheme = get_pass("PHEME_PASSWORD", f"user {user} pheme")
        if sudo is None:
            sudo = suresponder(c, rootpw=os.environ.get("ROOT_PASSWORD"))
        sudo(
            f"mount -t cifs -o user={user} -o pass={pheme} "
            f"//drive.irds.uwa.edu.au/sci-ms-001 {path}"
        )
        if c.run(f"test -d {path}/datastore", warn=True).failed:
            raise RuntimeError("failed to mount IRDS datastore")

        def umount():
            sudo(f"umount {path}")

        return umount
    return None


def remote_options(f):
    f = click.option(
        "--user", default="ianc", help="user on remote machine", show_default=True
    )(f)
    f = click.option(
        "-i",
        "--identity-file",
        help="SSH keyfile",
        # default="/home/ianc/.ssh/croppal",
        # show_default=True,
    )(f)
    # f = click.option(
    #     "--remote-ip",
    #     metavar="IP",
    #     default="130.95.176.97",
    #     help="remote host",
    #     show_default=True,
    # )(f)
    f = click.option(
        "-d",
        "--directory",
        metavar="DIRECTORY",
        default="/var/www/websites3/msmc",
        help="remote msmc directory",
        show_default=True,
    )(f)
    return f


SHARE_DIR = "instance/irds"

RemoteURL = namedtuple(
    "RemoteURL", ["broker_local_port", "backend_local_port", "broker", "result_backend"]
)


@cli.command()
@remote_options
@click.argument("remote-ip", required=False)
def unmount_irds(remote_ip, directory, user, identity_file, **kwargs):
    """Unmount IRDS datastore."""
    from fabric import Connection

    if remote_ip is None:
        remote_ip = "croppal"

    def make_connection():
        if IP.match(remote_ip):

            return Connection(
                remote_ip, user=user, connect_kwargs={"key_filename": identity_file},
            )
        return Connection(remote_ip)  # assume name

    path = SHARE_DIR
    with make_connection() as c:
        with c.cd(directory):
            if not c.run(f"test -d '{path}/datastore'", warn=True).failed:
                click.secho(f"unmounting {path}", fg="magenta")
                sudo = suresponder(c, rootpw=os.environ.get("ROOT_PASSWORD"))
                sudo(f"umount '{path}'")


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
