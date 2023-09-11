from __future__ import annotations

import subprocess
from shutil import which

import click

from .cli import cli
from .url import toURL
from .url import URL
from .utils import human

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
        if mysql is None:
            raise click.BadParameter("can't find mysql client")
        self.mysql = mysql
        self.cmds = cmds

    def run(self, query: str | None) -> list[list[str]]:
        db = self.url

        cmd = [self.mysql, f"--user={db.username}", f"--password={db.password}"]
        if db.port is not None:
            cmd.append(f"--port={db.port}")
        if db.database:
            cmd.append(db.database)
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


def db_size(url: str | URL, tables: list[str] | None) -> int:
    runner = MySQLRunner(url)

    query = MY2.format(db=runner.url.database)
    ret = runner.run(query)

    total = 0
    for name, num_bytes in ret[1:]:
        if tables is not None and name not in tables:
            continue
        total += int(num_bytes)
    return total


def get_db(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show databases")
    return [r[0] for r in ret[1:]]


def get_tables(url: str | URL) -> list[str]:
    runner = MySQLRunner(url)
    ret = runner.run("show tables")
    return [r[0] for r in ret[1:]]


@cli.group(
    help=click.style("mysql dump/load commands [requires sqlalchemy]", fg="magenta"),
)
def mysql():
    pass


@mysql.command(name="db-size")
@click.option("-t", "--tables", help="comma separated list of tables")
@click.option("-b", "--bytes", "asbytes", is_flag=True, help="output bytes")
@click.argument("url")
def db_size_cmd(url: str, tables: str | None, asbytes: bool):
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

    total = db_size(rurl, only)
    click.echo(str(total) if asbytes else human(total))


@mysql.command()
@click.argument("url")
def show_databases(url: str):
    print(get_db(url))
