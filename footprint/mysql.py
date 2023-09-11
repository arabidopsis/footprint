from __future__ import annotations

import os
import subprocess

import click

from .cli import cli
from .url import toURL
from .url import URL
from .utils import human
from .utils import which

MY = """
SELECT table_name as "table",
    table_rows as "rows",
    data_length  as "table_bytes",
    index_length as "index_bytes",
    data_length + index_length as "total_bytes",
    data_length / 1000 / 1000  as "table in MB",
    index_length / 1000 / 1000 as "index in MB",
    (data_length + index_length ) / 1000 / 1000 as "total in MB",
    data_free as "free bytes"
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""

MY2 = """
SELECT table_name as "table",
    data_length + index_length as "total_bytes"
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""
MY3 = """
SELECT
    sum(data_length + index_length) as "total_bytes"
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""


def mysql_cmd(mysql: str, db: URL, nodb: bool = False) -> list[str]:
    cmd = [mysql, f"--user={db.username}", f"--password={db.password}"]
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
        db = toURL(url)
        if db is None:
            raise click.BadArgumentUsage(f"can't parse url {url}")
        self.url = db
        mysql = which(mysqlcmd)
        self.mysql = mysql
        self.cmds = cmds

    def run(self, query: str | None, nodb: bool = False) -> list[list[str]]:
        db = self.url

        cmd = mysql_cmd(self.mysql, db, nodb=nodb)
        if self.cmds is not None:
            cmd = cmd + self.cmds
        p = subprocess.Popen(
            cmd,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
        )
        stdout, _ = p.communicate(query)
        if p.returncode != 0:
            raise RuntimeError(f"can't get data for {db.database}")
        ret = []
        for line in stdout.splitlines():
            lines = line.split("\t")
            ret.append(lines)
        return ret


def db_size(url: str | URL, tables: list[str] | None = None) -> int:
    runner = MySQLRunner(url)

    query = MY2.format(db=runner.url.database)
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
) -> list[tuple[str, int]]:
    runner = MySQLRunner(url)

    query = MY2.format(db=runner.url.database)
    ret = runner.run(query)

    r: list[tuple[str, int]] = []
    for name, num_bytes in ret[1:]:
        if tables is not None and name not in tables:
            continue
        total = int(num_bytes)
        r.append((name, total))
    return r


def get_db(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show databases")
    return [r[0] for r in ret[1:]]


def get_tables(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show tables")
    return [r[0] for r in ret[1:]]


def mysqlload(
    url_str: str,
    filename: str,
    drop: bool = False,
) -> tuple[int, int]:
    url = toURL(url_str)
    if url is None:
        raise ValueError(f"can't parse {url_str}")
    if url.database is None:
        raise ValueError(f"no database {url_str}")
    zcat = which("zcat")
    mysql = which("mysql")

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

    cmd = mysql_cmd(mysql, url)

    pmysql = subprocess.Popen(cmd, stdin=pzcat.stdout, stderr=subprocess.DEVNULL)
    if pzcat.stdout is not None:
        pzcat.stdout.close()

    # pmysql.communicate()
    if not waitfor([pmysql, pzcat]):
        raise RuntimeError(f"failed to load {filename}")

    size = db_size(url)

    return size, filesize


def mysqldump(
    url_str: str,
    directory: str | None = None,
    with_date: bool = False,
    tables: list[str] | None = None,
    postfix: str = "",
) -> tuple[int, int, str]:
    from datetime import datetime
    from .utils import rmfiles
    from pathlib import Path

    url = toURL(url_str)
    if url is None:
        raise ValueError(f"can't parse {url_str}")
    mysqldump = which("mysqldump")
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

    cmds = mysql_cmd(mysqldump, url)
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
        raise RuntimeError(f"failed to dump database {url.database}")

    filesize = outpath.stat().st_size

    total_bytes = db_size(url, tables)

    return total_bytes, filesize, outname


@cli.group(
    help=click.style("mysql dump/load commands", fg="magenta"),
)
def mysql():
    pass


@mysql.command(name="db-size")
@click.option("-f", "--full", is_flag=True, help="show table by table size")
@click.option("-t", "--tables", help="comma separated list of tables")
@click.option("-b", "--bytes", "asbytes", is_flag=True, help="output bytes")
@click.argument("url")
def db_size_cmd(url: str, tables: str | None, asbytes: bool, full: bool):
    """Print the database size."""
    only = [t.strip() for t in tables.split(",")] if tables else None
    rurl = toURL(url)
    if rurl is None:
        raise click.BadArgumentUsage(f"can't parse {url}")
    if only is not None:
        unknown = set(only) - set(get_tables(rurl))

        if unknown:
            raise click.BadParameter(
                f"unknown table name(s) {' '.join(unknown)}",
                param_hint="tables",
            )

    if not full:
        total = db_size(rurl, only)
        click.echo(str(total) if asbytes else human(total))
    else:
        ret = db_size_full(rurl, only)
        mx = max(map(len, [r[0] for r in ret]))
        tot = sum([r[1] for r in ret])
        ret = sorted(ret, key=lambda t: -t[1])
        ret.append(("total", tot))
        mx = max(mx, len("total"))
        for name, total in ret:
            n = len(name)
            pad = " " * (mx - n)
            v = str(total) if asbytes else human(total)
            click.echo(f"{name} {pad}: {v}")


@mysql.command()
@click.argument("url")
def databases(url: str):
    """List databases from URL."""
    for db in sorted(get_db(url)):
        print(db)


@mysql.command(name="load")
@click.option("--drop", is_flag=True, help="drop existing database first")
@click.argument("url")
@click.argument(
    "filename",
    type=click.Path(dir_okay=False, file_okay=True, exists=True),
)
def mysqload_cmd(url: str, filename: str, drop: bool) -> None:
    """Load a mysqldump."""

    total_bytes, filesize = mysqlload(url, filename, drop=drop)
    click.secho(
        f"loaded {human(filesize)} > {human(total_bytes)} from {filename}",
        fg="green",
        bold=True,
    )


@mysql.command(name="dump")
@click.option("-p", "--postfix", help="postfix this to database name", default="")
@click.option("--with-date", is_flag=True, help="add a date stamp to filename")
@click.option("-t", "--tables", help="comma separated list of tables")
@click.argument("url")
@click.argument("directory", required=False)
def mysqldump_cmd(
    url: str,
    directory: str | None,
    with_date: bool,
    postfix: str,
    tables: str | None,
) -> None:
    """Generate a mysqldump to a directory."""

    tbls: list[str] | None = None

    if tables is not None:
        tbls = [s.strip() for s in tables.split(",")]

    total_bytes, filesize, outname = mysqldump(
        url,
        directory,
        with_date=with_date,
        tables=tbls,
        postfix=postfix,
    )
    click.secho(
        f"dumped {human(total_bytes)} > {human(filesize)} as {outname}",
        fg="green",
        bold=True,
    )
