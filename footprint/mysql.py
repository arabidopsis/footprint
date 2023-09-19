from __future__ import annotations

import os
import subprocess
from dataclasses import replace

import click

from .cli import cli
from .url import make_url
from .url import URL
from .utils import human
from .utils import which

DB_SIZE = """
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


class MySQLError(RuntimeError):
    pass


def mysql_cmd(mysql: str, db: URL, nodb: bool = False) -> list[str]:
    cmd = [mysql]
    if db.username is not None:
        cmd.append(f"--user={db.username}")
    if db.password is not None:
        cmd.append(f"--password={db.password}")
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
        db = make_url(url)
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
        ret = []
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
) -> list[tuple[str, int]]:
    runner = MySQLRunner(url)

    query = DB_SIZE2.format(db=runner.url.database)
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
    ret = runner.run("show databases", nodb=True)
    return [r[0] for r in ret[1:]]


def get_tables(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show tables")
    return [r[0] for r in ret[1:]]


def mysqlload(
    url_str: str,
    filename: str,
    drop: bool = False,
    database: str | None = None,
) -> tuple[int, int]:
    url = make_url(url_str)
    if url is None:
        raise ValueError(f"can't parse {url_str}")
    if database is not None:
        url = replace(url, database=database)
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
        raise MySQLError(f"failed to load {filename}")

    size = db_size(url)

    return size, filesize


def mysqldump(
    url_str: str,
    directory: str | None = None,
    with_date: bool = False,
    tables: list[str] | None = None,
    postfix: str = "",
    database: str | None = None,
) -> tuple[int, int, str]:
    from datetime import datetime
    from .utils import rmfiles
    from pathlib import Path

    url = make_url(url_str)
    if url is None:
        raise ValueError(f"can't parse {url_str}")
    if database is not None:
        url = replace(url, database=database)
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
        row = [pad(v, l) for v, l in zip(row, max_lengths)]
        print(" ".join(row))
        if idx == 0:
            row = ["=" * (n + 1) for n in max_lengths]
            print(" ".join(row))


def totables(url: URL, tables: str | None) -> list[str] | None:
    if tables is None:
        return None
    only = [t.strip() for t in tables.split(",") if t.strip()]
    if not only:
        return None

    unknown = set(only) - set(get_tables(url))

    if unknown:
        raise click.BadParameter(
            f"unknown table name(s): {' '.join(unknown)}",
            param_hint="tables",
        )
    return only


@cli.group(
    help=click.style("mysql dump/load commands", fg="magenta"),
)
def mysql() -> None:
    pass


@mysql.command(name="db-size")
@click.option("-f", "--full", is_flag=True, help="show table by table size")
@click.option("-t", "--tables", help="comma separated list of tables")
@click.option("-b", "--bytes", "asbytes", is_flag=True, help="output bytes")
@click.option("-d", "--database", help="database to use (instead of url)")
@click.argument("url")
def db_size_cmd(
    url: str,
    tables: str | None,
    asbytes: bool,
    full: bool,
    database: str | None,
) -> None:
    """Print the database size."""
    rurl = make_url(url)
    if rurl is None:
        raise click.BadArgumentUsage(f"can't parse {url}")
    if database is not None:
        rurl = replace(rurl, database=database)
    only = totables(rurl, tables)

    if not full:
        total = db_size(rurl, only)
        click.echo(str(total) if asbytes else human(total))
    else:
        total_str = "Total"
        ret = db_size_full(rurl, only)

        mx = max(map(len, [r[0] for r in ret]))
        total = sum([r[1] for r in ret])
        mx = max(mx, len(total_str))

        ret = sorted(ret, key=lambda t: -t[1])
        ret.append((total_str, total))

        for name, total in ret:
            pad = " " * (mx - len(name))
            v = str(total) if asbytes else human(total)
            click.echo(f"{name} {pad}: {v}")


@mysql.command()
@click.argument("url")
def databases(url: str) -> None:
    """List databases from URL."""
    for db in sorted(get_db(url)):
        print(db)


@mysql.command(name="analyze")
@click.option("-d", "--database", help="database to use (instead of url)")
@click.argument("url")
def analyze_cmd(url: str, database: str | None) -> None:
    """Run `analyze table` over database"""
    rurl = make_url(url)
    if rurl is None:
        raise click.BadArgumentUsage(f"can't parse {url}")
    if database is not None:
        rurl = replace(rurl, database=database)
    tabulate(analyze(rurl))


@mysql.command(name="load")
@click.option("--drop", is_flag=True, help="drop existing database first")
@click.option("-d", "--database", help="database to use (instead of url)")
@click.argument("url")
@click.argument(
    "filename",
    type=click.Path(dir_okay=False, file_okay=True, exists=True),
)
def mysqload_cmd(url: str, filename: str, drop: bool, database: str | None) -> None:
    """Load a mysqldump."""

    total_bytes, filesize = mysqlload(url, filename, drop=drop, database=database)
    click.secho(
        f"loaded {human(filesize)} > {human(total_bytes)} from {filename}",
        fg="green",
        bold=True,
    )


@mysql.command(name="dump")
@click.option("-p", "--postfix", help="postfix this to database name", default="")
@click.option("--with-date", is_flag=True, help="add a date stamp to filename")
@click.option("-t", "--tables", help="comma separated list of tables")
@click.option("-d", "--database", help="database to use (instead of url)")
@click.argument("url")
@click.argument("directory", required=False)
def mysqldump_cmd(
    url: str,
    directory: str | None,
    with_date: bool,
    postfix: str,
    tables: str | None,
    database: str | None,
) -> None:
    """Generate a mysqldump to a directory."""

    rurl = make_url(url)
    if rurl is None:
        raise click.BadArgumentUsage(f"can't parse {url}")
    if database is not None:
        rurl = replace(rurl, database=database)

    tbls = totables(rurl, tables)

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


@mysql.command()
@click.argument("url")
@click.argument("query")
def query(
    url: str,
    query: str,
) -> None:
    """Run a query on a mysql database"""

    runner = MySQLRunner(url)
    result = runner.run(query)
    tabulate(result)
