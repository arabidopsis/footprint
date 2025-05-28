from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from typing import NamedTuple

import click

from .cli import cli
from .url import make_url
from .url import URL
from .utils import human
from .utils import which

DB_SIZE = """
SELECT table_name,
    table_rows,
    data_length,
    index_length,
    data_free
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""


DB_SIZE2 = """
SELECT table_name as "table",
    data_length + index_length as "total_bytes"
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""
DB_SIZE3 = """
SELECT
    sum(data_length + index_length) as "total_bytes"
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""


class MySQL:
    def __init__(self, host: str | None = None):
        self.host = host
        self._url: URL | None = None

    @property
    def url(self) -> URL:
        if self._url is not None:
            return self._url
        if self.host is None:
            raise click.BadOptionUsage("host", "please specify HOST")
        self._url = make_url(self.host)
        if self._url is None:
            raise click.BadOptionUsage("host", f"can't parse {self.host}")
        return self._url


def ensure_url(url: str | URL) -> URL:
    ret = make_url(url)
    if ret is None:
        raise click.BadOptionUsage("host", f"can't parse {url}")
    return ret


class Dbsize(NamedTuple):
    table_name: str
    table_rows: int
    data_length: int
    index_length: int
    data_free: int

    @property
    def total(self) -> int:
        return self.data_length + self.index_length


class MySQLError(RuntimeError):
    pass


def mysql_cmd(mysqlexe: str, db: URL, nodb: bool = False) -> list[str]:
    cmd = [mysqlexe]
    if db.username is not None:
        cmd.append(f"--user={db.username}")
    if db.password is not None:
        cmd.append(f"--password={db.password}")
    else:
        cmd.append("-p")
    if db.port is not None:
        cmd.append(f"--port={db.port}")
    if db.host:
        cmd.append(f"--host={db.host}")
    if db.database and not nodb:
        cmd.append(db.database)
    return cmd


def waitfor(procs: list[subprocess.Popen[bytes]]) -> bool:
    ok = True
    for p in procs:
        returncode = p.wait()
        if returncode != 0:
            ok = False
    return ok


class MySQLRunner:
    def __init__(
        self,
        url: str | URL,
        cmds: list[str] | None = None,
        mysqlcmd: str = "mysql",
    ):
        self.url = ensure_url(url)
        mysqlcmd = which(mysqlcmd)
        self.mysql = mysqlcmd
        self.cmds = cmds

    def run(self, query: str | None, nodb: bool = False) -> list[list[str]]:
        db = self.url

        cmd = mysql_cmd(self.mysql, db, nodb=nodb)
        if self.cmds is not None:
            cmd = cmd + self.cmds
        p = subprocess.Popen(
            cmd,
            # stderr=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = p.communicate(query)
        if p.returncode != 0:
            stderr = stderr.replace(
                "mysql: [Warning] Using a password on the command line interface can be insecure.",
                "",
            ).strip()
            raise MySQLError(stderr)
        ret: list[list[str]] = []
        for line in stdout.splitlines():
            lines = line.split("\t")
            ret.append(lines)
        return ret


def db_size(url: str | URL, tables: list[str] | None = None) -> int:
    runner = MySQLRunner(url)

    query = DB_SIZE2.format(db=runner.url.database)
    ret = runner.run(query)

    total = 0
    for name, num_bytes in ret[1:]:
        if tables is not None and name not in tables:
            continue
        total += int(num_bytes)
    return total


def db_size_full(
    url: str | URL,
    tables: list[str] | None = None,
) -> list[Dbsize]:
    runner = MySQLRunner(url)

    query = DB_SIZE.format(db=runner.url.database)
    ret = runner.run(query)
    # rows,bytes,index,total, free
    r: list[Dbsize] = []
    for row in ret[1:]:
        name = row[0]
        if tables is not None and name not in tables:
            continue
        vals = [int(r) for r in row[1:]]

        r.append(Dbsize(name, *vals))
    return r


def get_db(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show databases", nodb=True)
    return [r[0] for r in ret[1:]]


def get_tables(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show tables")
    return [r[0] for r in ret[1:]]


def mysqlload(
    url_str: str | URL,
    filename: str,
    drop: bool = False,
    database: str | None = None,
) -> tuple[int, int]:
    url = ensure_url(url_str)

    if database is not None:
        url = replace(url, database=database)
    if url.database is None:
        raise ValueError(f"no database {url_str}")
    zcat = which("zcat")
    mysqlcmd = which("mysql")

    filesize = os.stat(filename).st_size

    r = MySQLRunner(url)
    if drop:
        r.run(f"drop database if exists {url.database}", nodb=True)
    r.run(
        f"create database if not exists {url.database} character set=latin1",
        nodb=True,
    )

    pzcat = subprocess.Popen(
        [zcat, filename],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    cmd = mysql_cmd(mysqlcmd, url)

    pmysql = subprocess.Popen(cmd, stdin=pzcat.stdout, stderr=subprocess.DEVNULL)
    if pzcat.stdout is not None:
        pzcat.stdout.close()

    # pmysql.communicate()
    if not waitfor([pmysql, pzcat]):
        raise MySQLError(f"failed to load {filename}")

    size = db_size(url)

    return size, filesize


def mysqldump(
    url_str: str | URL,
    directory: str | None = None,
    with_date: bool = False,
    tables: list[str] | None = None,
    postfix: str = "",
    database: str | None = None,
) -> tuple[int, int, str]:
    from datetime import datetime
    from .utils import rmfiles
    from pathlib import Path

    url = ensure_url(url_str)
    if database is not None:
        url = replace(url, database=database)
    mysqldumpcmd = which("mysqldump")
    gzip = which("gzip")

    if postfix and not postfix.startswith("-"):
        postfix = "-" + postfix

    if with_date:
        now = datetime.now()
        outname = (
            f"{url.database}{postfix}-{now.year}-{now.month:02}-{now.day:02}.sql.gz"
        )
    else:
        outname = f"{url.database}{postfix}.sql.gz"

    directory = directory or "."

    pth = Path(directory)

    if not pth.exists():
        pth.mkdir(parents=True, exist_ok=True)

    outpath = pth / outname

    cmds = mysql_cmd(mysqldumpcmd, url)
    cmds.extend(["--max_allowed_packet=32M", "--single-transaction"])
    if tables:
        cmds.extend(tables)

    with outpath.open("wb") as fp:
        pmysql = subprocess.Popen(
            cmds,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
        pgzip = subprocess.Popen(
            [gzip],
            stdin=pmysql.stdout,
            stderr=subprocess.DEVNULL,
            stdout=fp,
        )
        if pmysql.stdout is not None:
            pmysql.stdout.close()

    if not waitfor([pmysql, pgzip]):
        rmfiles([str(outpath)])
        raise MySQLError(f"failed to dump database {url.database}")

    filesize = outpath.stat().st_size

    total_bytes = db_size(url, tables)

    return total_bytes, filesize, outname


def analyze(url: URL) -> list[list[str]]:
    tables = ",".join(get_tables(url))
    runner = MySQLRunner(url)

    return runner.run(f"analyze table {tables}")


def tabulate(result: list[list[str]]) -> None:
    def pad(val: str, length: int) -> str:
        p = " " * (length + 1 - len(val))
        return f"{val}{p}"

    if not result:
        return
    max_lengths = [0] * len(result[0])

    for row in result:
        lengths = [len(r) for r in row]
        max_lengths = [max(l1, l2) for l1, l2 in zip(lengths, max_lengths)]

    for idx, row in enumerate(result):
        row = [pad(v, ml) for v, ml in zip(row, max_lengths)]
        print(" ".join(row))
        if idx == 0:
            row = ["=" * (n + 1) for n in max_lengths]
            print(" ".join(row))


def totables(url: URL, tables: tuple[str, ...]) -> list[str] | None:
    only = [t.strip() for tt in tables for t in tt.split(",") if t.strip()]
    if not only:
        return None

    unknown = set(only) - set(get_tables(url))

    if unknown:
        raise click.BadParameter(
            f"unknown table name(s): {' '.join(unknown)}",
            param_hint="tables",
        )
    return only


pass_mysql = click.make_pass_decorator(MySQL)


@cli.group(help=click.style("mysql dump/load commands", fg="magenta"))
@click.option(
    "-h",
    "--host",
    metavar="HOST",
    help="database URL [envvar=HOST]",
    envvar="HOST",
)
@click.pass_context
def mysql(ctx: click.Context, host: str | None) -> None:
    ctx.obj = MySQL(host)


@mysql.command(name="db-size")
@click.option("-s", "--summary", is_flag=True, help="show database total only")
@click.option("-t", "--tables", help="comma separated list of tables", multiple=True)
@click.option("-b", "--bytes", "asbytes", is_flag=True, help="output bytes")
@click.option("-d", "--database", help="database to use (instead of url)")
@pass_mysql
def db_size_cmd(
    db: MySQL,
    tables: tuple[str, ...],
    asbytes: bool,
    summary: bool,
    database: str | None,
) -> None:
    """Print the database size."""
    url = db.url
    if database is not None:
        url = replace(url, database=database)

    only = totables(url, tables)

    if summary:
        total = db_size(url, only)
        click.echo(str(total) if asbytes else human(total))
    else:
        ret = db_size_full(url, only)
        ret = sorted(ret, key=lambda t: -t.total)
        tot = Dbsize(
            table_name="Total",
            table_rows=sum(r.table_rows for r in ret),
            data_length=sum(r.data_length for r in ret),
            index_length=sum(r.index_length for r in ret),
            data_free=sum(r.data_free for r in ret),
        )
        ret.append(tot)
        mx = max(len(r.table_name) for r in ret)

        for r in ret:
            pad = " " * (mx - len(r.table_name))
            v = str(r.total) if asbytes else human(r.total)
            click.echo(f"{r.table_name} {pad}: {v}")


@mysql.command()
@pass_mysql
def databases(db: MySQL) -> None:
    """List databases from URL."""
    for database in sorted(get_db(db.url)):
        print(database)


@mysql.command(name="analyze")
@click.option("-d", "--database", help="database to use (instead of url)")
@pass_mysql
def analyze_cmd(db: MySQL, database: str | None) -> None:
    """Run `analyze table` over database"""
    rurl = db.url
    if database is not None:
        rurl = replace(rurl, database=database)
    tabulate(analyze(rurl))


@mysql.command(name="load")
@click.option("--drop", is_flag=True, help="drop existing database first")
@click.option("-d", "--database", help="database to use (instead of url)")
@click.argument(
    "filename",
    type=click.Path(dir_okay=False, file_okay=True, exists=True),
)
@pass_mysql
def mysqload_cmd(db: MySQL, filename: str, drop: bool, database: str | None) -> None:
    """Load a mysqldump."""

    total_bytes, filesize = mysqlload(db.url, filename, drop=drop, database=database)
    click.secho(
        f"loaded {human(filesize)} > {human(total_bytes)} from {filename}",
        fg="green",
        bold=True,
    )


@mysql.command(name="dump")
@click.option("-p", "--postfix", help="postfix this to database name", default="")
@click.option("--with-date", is_flag=True, help="add a date stamp to filename")
@click.option("-t", "--tables", help="comma separated list of tables", multiple=True)
@click.option("-d", "--database", help="database to use (instead of url)")
@click.argument("directory", required=False)
@pass_mysql
def mysqldump_cmd(
    db: MySQL,
    directory: str | None,
    with_date: bool,
    postfix: str,
    tables: tuple[str, ...],
    database: str | None,
) -> None:
    """Generate a mysqldump to a directory."""
    url = db.url
    if database is not None:
        url = replace(url, database=database)

    tbls = totables(url, tables)

    total_bytes, filesize, outname = mysqldump(
        url,
        directory,
        with_date=with_date,
        tables=tbls,
        postfix=postfix,
        database=database,
    )
    click.secho(
        f"dumped {human(total_bytes)} > {human(filesize)} as {outname}",
        fg="green",
        bold=True,
    )


@mysql.command("query")
@click.argument("query", required=False)
@pass_mysql
def query_cmd(
    db: MySQL,
    query: str | None,
) -> None:
    """Run a query on a mysql database"""
    import sys

    if query is None:
        query = sys.stdin.read()
    runner = MySQLRunner(db.url)
    result = runner.run(query)
    tabulate(result)
