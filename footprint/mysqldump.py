import typing as t

import click

from .cli import cli
from .config import RANDOM_PORT
from .utils import connect_to, human, is_local, make_connection


def mysqldump(
    url_str: str,
    directory: str,
    with_date: bool = False,
    tables: t.Optional[t.List[str]] = None,
    postfix: str = "",
) -> t.Tuple[int, int, str]:

    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .dbsize import my_dbsize
    from .utils import mysqlresponder, update_url

    url = make_url(url_str)
    machine = url.host
    url = update_url(url, host="localhost")
    if postfix and not postfix.startswith("-"):
        postfix = "-" + postfix

    if with_date:
        now = datetime.now()
        outname = (
            f"{url.database}{postfix}-{now.year}-{now.month:02}-{now.day:02}.sql.gz"
        )
    else:
        outname = f"{url.database}{postfix}.sql.gz"
    if tables is not None:
        ts = " ".join(f"{s}" for s in tables)
    else:
        ts = ""
    cmd = f"""mysqldump --max_allowed_packet=32M --single-transaction \\
    --user={url.username} --port={url.port or 3306} -h {url.host} -p {url.database} {ts} | gzip > {outname}"""

    directory = directory or "."
    islocal = machine in {"127.0.0.1", "localhost"}

    with make_connection(machine=None if islocal else machine) as c:
        c.run(f"test -d '{directory}' || mkdir -p '{directory}'")
        with c.cd(directory):
            mysqlrun = mysqlresponder(c, url.password)
            if mysqlrun(cmd, warn=True).failed:
                c.run(f"rm -f {outname}", warn=True)
                raise RuntimeError(f"failed to archive {url.database}")
            filesize = int(c.run(f"stat -c%s {outname}", hide=True).stdout.strip())

        with c.forward_local(RANDOM_PORT, 3306):
            if not islocal:
                url = update_url(url, host="127.0.0.1", port=RANDOM_PORT)
            total_bytes = my_dbsize(url.database, create_engine(url), tables).sum(
                axis=0
            )["total_bytes"]

    return total_bytes, filesize, outname


def read_tables(filename: str) -> t.List[str]:
    ret = []
    with open(filename) as fp:
        for line in fp:
            if line.startswith("#"):
                continue
            ret.append(line.strip())
    return ret


def mysql_cmd(url, cmd: t.Optional[str] = None) -> str:
    if cmd is not None:
        cmd = f" -e '{cmd}'"
    else:
        cmd = ""
    return f"""mysql --user={url.username} --port={url.port or 3306} -h {url.host} -p{cmd}"""


def mysqlload(
    url_str: str, filename: str, database: t.Optional[str] = None, drop: bool = False
) -> t.Tuple[int, int]:

    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .dbsize import my_dbsize
    from .utils import mysqlresponder, update_url

    url = make_url(url_str)

    machine = url.host
    database = database or url.database
    url = update_url(url, host="localhost")

    dropdb = mysql_cmd(url, f"drop database if exists {database}")
    createdb = mysql_cmd(
        url, f"create database if not exists {database} character set=latin1"
    )
    cmd = f"zcat '{filename}' | {mysql_cmd(url)} {database}"

    with make_connection(machine) as c:
        if c.run(f"test -f '{filename}'", warn=True).failed:
            raise FileNotFoundError(filename)
        filesize = int(c.run(f"stat -c%s {filename}", hide=True).stdout.strip())
        mysqlrun = mysqlresponder(c, url.password)

        if drop:
            mysqlrun(dropdb)
        mysqlrun(createdb, warn=True, hide=True)
        mysqlrun(cmd)
        with c.forward_local(RANDOM_PORT, 3306):
            if not is_local(machine):
                url = update_url(url, host="127.0.0.1", port=RANDOM_PORT)

            total_bytes = my_dbsize(url.database, create_engine(url)).sum(axis=0)[
                "total_bytes"
            ]

    return total_bytes, filesize


def geturl(machine, directory, keys=None):
    def ok(key):
        return key not in {"SECRET_KEY"}

    with make_connection(machine) as c:
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


def execute_url(url: str, query: str) -> t.Iterator[t.Any]:
    from sqlalchemy import text

    with connect_to(url) as engine:
        with engine.connect() as conn:
            yield from conn.execute(text(query))


def get_db(url: str) -> t.List[str]:
    return [r[0] for r in execute_url(url, "show databases")]


def get_tables(url: str) -> t.List[str]:
    return [r[0] for r in execute_url(url, "show tables")]


@cli.group(help=click.style("mysql dump/load commands", fg="magenta"))
def mysql():
    pass


@mysql.command(name="dump")
@click.option("-p", "--postfix", help="postfix this to database name", default="")
@click.option("--with-date", is_flag=True, help="add a date stamp to filename")
@click.option("-t", "--tables", help="list of tables or csv file")
@click.argument("url")
@click.argument("directory")
def mysqldump_(
    url: str, directory: str, with_date: bool, postfix: str, tables: t.Optional[str]
) -> None:
    """Generate a mysqldump to remote directory."""
    import os

    tbls: t.Optional[t.List[str]] = None

    if tables is not None:
        if os.path.isfile(tables):
            tbls = read_tables(tables)
        else:
            tbls = [s.strip() for s in tables.split(",")]

    total_bytes, filesize, outname = mysqldump(
        url, directory, with_date=with_date, tables=tbls, postfix=postfix
    )
    click.secho(
        f"dumped {human(total_bytes)} > {human(filesize)} as {outname}",
        fg="green",
        bold=True,
    )


@mysql.command(name="load")
@click.option("--drop", is_flag=True, help="drop existing database first")
@click.option("-d", "--database", help="put tables into this database")
@click.argument("url")
@click.argument("filename")
def mysqload_(url: str, filename: str, drop: bool, database: str) -> None:
    """Load a mysqldump."""

    total_bytes, filesize = mysqlload(url, filename, database=database, drop=drop)
    click.secho(
        f"loaded {human(filesize)} > {human(total_bytes)} from {filename}",
        fg="green",
        bold=True,
    )


@mysql.command(name="url")
@click.argument("machine")
@click.argument("directory")
def url_(machine: str, directory: str) -> None:
    """Find database URL."""

    click.echo(geturl(machine, directory))


@mysql.command()
@click.argument("url")
def databases(url: str):
    """Find database URL."""
    for db in sorted(get_db(url)):
        print(db)


@mysql.command(name="tables")
@click.argument("url")
def tables_(url: str):
    """Find tables URL."""
    for tbl in sorted(get_tables(url)):
        print(tbl)
