import typing as t

import click

from .cli import cli
from .config import RANDOM_PORT
from .utils import human

if t.TYPE_CHECKING:
    from pandas import DataFrame  # pylint: disable=unused-import
    from sqlalchemy import MetaData  # pylint: disable=unused-import
    from sqlalchemy.engine import Engine  # pylint: disable=unused-import

# from https://wiki.postgresql.org/wiki/Disk_Usage

PG = """
SELECT table_name as table, row_estimate as rows, total_bytes, toast_bytes, table_bytes,
      pg_size_pretty(total_bytes) AS total
    , pg_size_pretty(index_bytes) AS INDEX
    , pg_size_pretty(toast_bytes) AS toast
    , pg_size_pretty(table_bytes) AS TABLE
  FROM (
  SELECT *, total_bytes - index_bytes - COALESCE(toast_bytes,0) AS table_bytes FROM (
      SELECT  relname AS TABLE_NAME
              , cast(c.reltuples as bigint)  AS row_estimate
              , pg_total_relation_size(c.oid) AS total_bytes
              , pg_indexes_size(c.oid) AS index_bytes
              , pg_total_relation_size(reltoastrelid) AS toast_bytes
          FROM pg_class c
          LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE relkind = 'r' and nspname = '{db}'
  ) a
) a order by total_bytes desc"""


def pg_dbsize(db: str, engine: "Engine") -> "DataFrame":
    import pandas as pd
    from sqlalchemy import text

    return pd.read_sql_query(text(PG.format(db=db)), con=engine)


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


def my_dbsize(
    database: str, engine: "Engine", tables: t.Optional[t.List[str]] = None
) -> "DataFrame":
    import pandas as pd
    from sqlalchemy import text

    q = MY.format(db=database)
    if tables is not None:
        tls = ",".join(f'"{t}"' for t in tables)
        q += f" AND table_name IN ({tls})"

    return pd.read_sql_query(text(q), con=engine)


def db_size(
    url: str, schema: t.Optional[str] = None, machine: t.Optional[str] = None
) -> "DataFrame":
    from fabric import Connection
    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .utils import update_url

    u = make_url(url)
    db = schema or u.database
    machine = machine or u.host
    is_mysql = u.drivername.startswith("mysql")
    port = u.port or (3306 if is_mysql else 5432)

    def run(url):
        e = create_engine(str(url))
        if is_mysql:
            df = my_dbsize(db, e)
        else:
            df = pg_dbsize(db, e)
        return df

    if machine not in {"localhost", "127.0.0.1"}:
        with Connection(machine) as c:
            with c.forward_local(local_port=RANDOM_PORT, remote_port=port):
                u = update_url(u, host="127.0.0.1", port=RANDOM_PORT)
                return run(u)

    return run(u)


def show(table: str, meta: "MetaData", engine: "Engine", limit: int = 100) -> None:
    import pandas as pd
    from sqlalchemy import select
    from sqlalchemy.schema import CreateTable

    if "." in table:
        _, tname = table.split(".")
    else:
        _, tname = engine.url.database, table
    if table not in meta:
        meta.reflect(only=[tname], bind=engine)
    tt = meta.tables[table]
    q = select([tt]).limit(limit)
    print(str(CreateTable(tt).compile(engine)))

    # txt = "select indexdef from pg_indexes where tablename = '{tname}' and schemaname = '{schema}'".format(
    #     tname=tname, schema=schema
    # )
    # with engine.connect() as conn:
    #     res = conn.execute(text(txt)).fetchall()

    # for r in res:
    #     print(r.indexdef)
    # if res:
    #     print()
    idx = [c.name for c in tt.primary_key]
    if idx:
        df = pd.read_sql_query(q, engine, index_col=idx)
    else:
        df = pd.read_sql_query(q, engine)
    print(df.to_string())


@cli.command(name="db-size")
@click.option("--full", is_flag=True, help="show full output")
# @click.option("--all", "all_db", is_flag=True, help="show all databases")
@click.option("-s", "--schema", help="schema")
@click.option("-m", "--machine", help="machine")
@click.option("-b", "--bytes", "asbytes", is_flag=True, help="output bytes")
@click.argument("url")
def db_size_cmd(
    url: str,
    full: bool,
    schema: t.Optional[str],
    machine: t.Optional[str],
    asbytes: bool,
):
    """Print the database size."""
    df = db_size(url, schema, machine)
    if full:
        click.echo(df.to_string())
    total = df["total_bytes"].sum()
    # for i in totals.index:
    #     print(i, totals[i])

    click.echo(total if asbytes else human(total))


@cli.command(name="tables")
@click.option("--url", help="db url")
@click.option("--schema")
@click.option(
    "--limit", default=100, show_default=True, help="show only this many rows"
)
@click.argument("tables", nargs=-1)
def show_tables(
    tables: t.List[str], url: t.Optional[str], limit: int, schema: t.Optional[str]
) -> None:
    """Show table metadata and rows."""
    from sqlalchemy import (  # pylint: disable=redefined-outer-name
        MetaData,
        create_engine,
    )

    if url is None:
        raise click.BadOptionUsage("url", "require database url")

    e = create_engine(url)
    if not tables:
        if not schema:
            schema = e.url.database  # pylint: disable=no-member
        m = MetaData(schema=schema)
        m.reflect(bind=e)
        tables = sorted(m.tables.keys())
    else:

        def s(tname):
            if "." in tname:
                return tname.split(".")
            return None, tname

        tt = [s(t) for t in tables]

        s = {t[0] for t in tt}.pop()
        if s is None:
            s = schema
        m = MetaData(schema=schema)

    for table in tables:
        show(table, m, e, limit=limit)
