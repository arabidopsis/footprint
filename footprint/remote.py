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


def create_urls(broker_port, backend_port):
    from sqlalchemy.engine.url import make_url
    from ..utils_flask import create_and_config

    config = create_and_config()
    cc = config["CELERY_CONFIG"]
    result_backend = cc["result_backend"]
    broker_url = cc["broker_url"]
    if not result_backend.startswith("db+"):
        raise RuntimeError(f"can't tunnel {result_backend}")

    broker_url = make_url(broker_url)
    result_backend = make_url(result_backend)

    broker_local_port = broker_url.port or 6379
    backend_local_port = result_backend.port or 3306

    if hasattr(broker_url, "set"):
        broker_url = broker_url.set(  # pylint: disable=no-member
            host="127.0.0.1", port=broker_port
        )
        result_backend = result_backend.set(  # pylint: disable=no-member
            host="127.0.0.1", port=backend_port
        )
    else:
        broker_url.host = "127.0.0.1"
        broker_url.port = broker_port

        result_backend.host = "127.0.0.1"
        result_backend.port = backend_port

    return RemoteURL(broker_local_port, backend_local_port, broker_url, result_backend)

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
                remote_ip,
                user=user,
                connect_kwargs={"key_filename": identity_file},
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
@click.option("-d", "--directory", default=".")
@click.argument("machine")
def install_repo(machine, directory):
    """Install this repo on a remote machine."""
    from fabric import Connection

    with Connection(machine) as c:
        if directory != ".":
            c.run(f'mkdir -p "{directory}"')
        with c.cd(directory):
            r = c.local(
                "git config --get remote.origin.url", warn=True, hide=True
            ).stdout.strip()
            c.run(f"git clone {r}", pty=True)
