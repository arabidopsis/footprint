import click
from .cli import cli


def mysqldump(url, directory):

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url
    from fabric import Connection
    from datetime import datetime
    from .dbsize import my_dbsize

    random_port = 17013

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
        with c.forward_local(random_port, 3306):
            url.port = random_port
            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize, outname


def mysqlload(url, filename):

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url
    from fabric import Connection
    from .dbsize import my_dbsize

    random_port = 17013

    url = make_url(url)
    machine = url.host
    url.host = "localhost"
    db = """mysql \\
    --user=%s --port=%d -h %s --password=%s -e 'create database if not exists %s'""" % (
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
        c.run(db, pty=True, warn=True, hide=True)
        c.run(cmd, pty=True)
        with c.forward_local(random_port, 3306):
            url.port = random_port
            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize


def geturl(machine, directory):
    from fabric import Connection

    # from flask import Config

    with Connection(machine) as c:
        with c.cd(directory):
            lines = c.run("ls instance", hide=True).stdout.splitlines()
            cfg = [l for l in lines if l.endswith(".cfg")][0]
            g = {}
            txt = c.run(f"cat instance/{cfg}", hide=True).stdout
            exec(txt, g)
            # keys = {k for k in g.keys() if k.isupper()}
            # print(keys)
            return g.get("SQLALCHEMY_DATABASE_URI")


@cli.command(name="mysqldump")
@click.option("-d", "--directory", help="remote directory")
@click.argument("url")
def mysqldump_(url, directory):
    """Generate a mysqldump."""
    from .utils import human

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
    """Generate a mysqldump."""
    from .utils import human

    total_bytes, filesize = mysqlload(url, filename)
    click.secho(
        f"loaded  {human(filesize)} > {human(total_bytes)} from {filename}",
        fg="green",
        bold=True,
    )


@cli.command(name="url")
@click.argument("machine")
@click.argument("directory")
def url_(machine, directory):

    click.echo(geturl(machine, directory))
