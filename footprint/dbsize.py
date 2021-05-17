import click
from .cli import cli


# from https://wiki.postgresql.org/wiki/Disk_Usage

PG = """
SELECT table_name, row_estimate, total_bytes, toast_bytes, table_bytes,
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


def pg_dbsize(db, engine):
    import pandas as pd
    from sqlalchemy import text

    return pd.read_sql_query(text(PG.format(db=db)), con=engine)


MY = """
SELECT table_name as "table",
    table_rows as "rows",
    data_length  as "table bytes",
    index_length as "index bytes",
    data_length + index_length as "total bytes",
    data_length / 1024 / 1024  as "table in MB",
    index_length / 1024 / 1024 as "index in MB",
    (data_length + index_length ) / 1024 / 1024 as "total in MB",
    data_free as "free bytes"
FROM information_schema.TABLES
WHERE table_schema = '{db}'
"""


def my_dbsize(db, engine):
    import pandas as pd
    from sqlalchemy import text

    return pd.read_sql_query(text(MY.format(db=db)), con=engine)


def show(table, meta, engine, limit=100):
    import pandas as pd
    from sqlalchemy import select, text
    from sqlalchemy.schema import CreateTable

    schema, tname = table.split(".")
    if table not in meta:
        meta.reflect(only=[tname], bind=engine)
    t = meta.tables[table]
    q = select([t]).limit(limit)
    print(str(CreateTable(t).compile(engine)))

    txt = "select indexdef from pg_indexes where tablename = '{tname}' and schemaname = '{schema}'".format(
        tname=tname, schema=schema
    )
    with engine.connect() as conn:
        res = conn.execute(text(txt)).fetchall()

    for r in res:
        print(r.indexdef)
    if res:
        print()
    df = pd.read_sql_query(q, engine, index_col=[c.name for c in t.primary_key])
    print(df.to_string())


@cli.command()
@click.option("--full", is_flag=True, help="show full output")
@click.option("-s", "--schema", help="schema")
@click.argument("url")
def db_size(url, full, schema):
    """Print the database sizes."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    u = make_url(url)
    db = schema or u.database

    e = create_engine(url)
    if u.drivername.startswith("mysql"):
        df = my_dbsize(db, e)
    else:
        df = pg_dbsize(db, e)
    if full:
        print(df.to_string())
    print(df.drop(["table"], axis="columns").sum(axis=0))


@cli.command(name="tables")
@click.option("--url", help="db url")
@click.option("--schema")
@click.option("--limit", default=100, show_default=True, help="show only this many rows")
@click.argument("tables", nargs=-1)
def show_tables(tables, url, limit, schema):
    """Show table metadata."""
    from sqlalchemy import create_engine, MetaData

    e = create_engine(url)
    if not tables and schema:
        m = MetaData(schema=schema)
        m.reflect(bind=e)
        tables = sorted(m.tables.keys())
    else:

        def s(t):
            if "." in t:
                return t.split(".")
            return None, t

        tt = [s(t) for t in tables]

        s = {t[0] for t in tt}.pop()
        if s is None:
            s = schema
        m = MetaData(schema=schema)

    for t in tables:
        show(t, m, e, limit=limit)
