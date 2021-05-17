import click
from fabric import Connection

from .cli import cli
from .utils import human

RANDOM_PORT = 17013


def mysqldump(url, directory):

    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .dbsize import my_dbsize

    url = make_url(url)
    machine = url.host
    url.host = "localhost"
    now = datetime.now()
    outname = f"{url.database}-{now.year}-{now.month:02}-{now.day:02}.sql.gz"

    cmd = """mysqldump --max_allowed_packet=32M --single-transaction \\
    --user=%s --port=%d -h %s --password=%s %s | gzip > %s""" % (
        url.username,
        url.port or 3306,
        url.host,
        url.password,
        url.database,
        outname,
    )
    directory = directory or "."
    with Connection(machine) as c:
        c.run(f"test -d '{directory}' || mkdir -p '{directory}'")
        with c.cd(directory):
            c.run(cmd, pty=True)
            filesize = int(c.run(f"stat -c%s {outname}", hide=True).stdout.strip())
        with c.forward_local(RANDOM_PORT, 3306):
            url.port = RANDOM_PORT
            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize, outname


def mysqlload(url, filename):

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .dbsize import my_dbsize

    url = make_url(url)
    machine = url.host
    url.host = "localhost"
    createdb = """mysql \\
    --user=%s --port=%d -h %s --password=%s -e 'create database if not exists %s character set=latin1'""" % (
        url.username,
        url.port or 3306,
        url.host,
        url.password,
        url.database,
    )
    cmd = """zcat %s | mysql \\
    --user=%s --port=%d -h %s --password=%s %s""" % (
        filename,
        url.username,
        url.port or 3306,
        url.host,
        url.password,
        url.database,
    )
    with Connection(machine) as c:
        c.run(f"test -f '{filename}'")
        filesize = int(c.run(f"stat -c%s {filename}", hide=True).stdout.strip())
        c.run(createdb, pty=True, warn=True, hide=True)
        c.run(cmd, pty=True)
        with c.forward_local(RANDOM_PORT, 3306):
            url.port = RANDOM_PORT
            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize


def geturl(machine, directory):

    with Connection(machine) as c:
        with c.cd(directory):
            lines = c.run("ls instance", hide=True).stdout.splitlines()
            cfg = [l for l in lines if l.endswith(".cfg")][0]
            txt = c.run(f"cat instance/{cfg}", hide=True).stdout
            g = {}
            exec(compile(txt, cfg, "exec"), g)  # pylint: disable=exec-used
            return g.get("SQLALCHEMY_DATABASE_URI")


@cli.command(name="mysqldump")
@click.argument("url")
@click.argument("directory")
def mysqldump_(url, directory):
    """Generate a mysqldump to remote directory."""

    total_bytes, filesize, outname = mysqldump(url, directory)
    click.secho(
        f"dumped {human(total_bytes)} > {human(filesize)} as {outname}",
        fg="green",
        bold=True,
    )


@cli.command(name="mysqlload")
@click.argument("url")
@click.argument("filename")
def mysqload_(url, filename):
    """Load a mysqldump."""

    total_bytes, filesize = mysqlload(url, filename)
    click.secho(
        f"loaded {human(filesize)} > {human(total_bytes)} from {filename}",
        fg="green",
        bold=True,
    )


@cli.command(name="url")
@click.argument("machine")
@click.argument("directory")
def url_(machine, directory):

    click.echo(geturl(machine, directory))
