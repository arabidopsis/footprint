import click
from fabric import Connection
from invoke import Context as IContext

from .cli import cli
from .config import RANDOM_PORT
from .utils import connect_to, human


class Context(IContext):
    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        pass

    def forward_local(self, *args, **kwargs):
        return self


def mysqldump(url, directory, with_date=False):

    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .dbsize import my_dbsize
    from .utils import mysqlresponder, update_url

    url = make_url(url)
    machine = url.host
    url.host = "localhost"
    if with_date:
        now = datetime.now()
        outname = f"{url.database}-{now.year}-{now.month:02}-{now.day:02}.sql.gz"
    else:
        outname = f"{url.database}.sql.gz"

    cmd = """mysqldump --max_allowed_packet=32M --single-transaction \\
    --user=%s --port=%d -h %s -p %s | gzip > %s""" % (
        url.username,
        url.port or 3306,
        url.host,
        url.database,
        outname,
    )
    directory = directory or "."
    islocal = machine in {"127.0.0.1", "localhost"}

    def make_connection():

        if not islocal:
            return Connection(machine)
        return Context()

    with make_connection() as c:
        c.run(f"test -d '{directory}' || mkdir -p '{directory}'")
        with c.cd(directory):
            mysqlrun = mysqlresponder(c, url.password)
            mysqlrun(cmd, pty=True)
            filesize = int(c.run(f"stat -c%s {outname}", hide=True).stdout.strip())

        with c.forward_local(RANDOM_PORT, 3306):
            if not islocal:
                url = update_url(url, host="127.0.0.1", port=RANDOM_PORT)
            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize, outname


def mysqlload(url, filename):

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .dbsize import my_dbsize
    from .utils import mysqlresponder, update_url

    url = make_url(url)

    machine = url.host
    url.host = "localhost"
    createdb = """mysql \\
    --user=%s --port=%d -h %s -p -e 'create database if not exists %s character set=latin1'""" % (
        url.username,
        url.port or 3306,
        url.host,
        url.database,
    )
    cmd = """zcat %s | mysql \\
    --user=%s --port=%d -h %s -p %s""" % (
        filename,
        url.username,
        url.port or 3306,
        url.host,
        url.database,
    )
    islocal = machine in {"127.0.0.1", "localhost"}

    def make_connection():

        if not islocal:
            return Connection(machine)
        return Context()

    with make_connection() as c:
        if c.run(f"test -f '{filename}'", warn=True).failed:
            raise FileNotFoundError(filename)
        filesize = int(c.run(f"stat -c%s {filename}", hide=True).stdout.strip())
        mysqlrun = mysqlresponder(c, url.password)
        mysqlrun(createdb, pty=True, warn=True, hide=True)
        mysqlrun(cmd, pty=True)
        with c.forward_local(RANDOM_PORT, 3306):
            if not islocal:
                url = update_url(url, host="127.0.0.1", port=RANDOM_PORT)

            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize


def geturl(machine, directory, keys=None):
    def ok(key):
        return key not in {"SECRET_KEY"}

    with Connection(machine) as c:
        with c.cd(directory):
            # lines = c.run("ls instance", hide=True).stdout.splitlines()
            txt = c.run("cat instance/*.cfg", hide=True).stdout
            g = {}
            exec(compile(txt, "config.cfg", "exec"), g)  # pylint: disable=exec-used
            g = {
                k: v
                for k, v in g.items()
                if ok(k) and k.isupper() and (keys is None or k in keys)
            }

            return g


def get_db(url):
    with connect_to(url) as engine:
        with engine.connect() as con:
            dbs = [r[0] for r in con.execute("show databases")]
    return dbs


@cli.group(help=click.style("mysql dump/load commands", fg="magenta"))
def mysql():
    pass


@mysql.command(name="dump")
@click.option("--with-date", is_flag=True, help="add a date stamp to filename")
@click.argument("url")
@click.argument("directory")
def mysqldump_(url, directory, with_date):
    """Generate a mysqldump to remote directory."""

    total_bytes, filesize, outname = mysqldump(url, directory, with_date=with_date)
    click.secho(
        f"dumped {human(total_bytes)} > {human(filesize)} as {outname}",
        fg="green",
        bold=True,
    )


@mysql.command(name="load")
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
    """Find database URL."""

    click.echo(geturl(machine, directory))


@cli.command()
@click.argument("url")
def databases(url):
    """Find database URL."""
    for db in sorted(get_db(url)):
        print(db)
