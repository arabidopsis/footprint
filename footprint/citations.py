import re
import time

import click
import pandas as pd
import requests

from .cli import cli

DOI = re.compile(r"coci => ([^\s]+)$")


def fetch_opennet(doi):
    r = requests.get(f"https://w3id.org/oc/index/api/v1/citations/{doi}")
    r.raise_for_status()
    return r.json()


def fetch_crossref(doi):
    r = requests.get(f"https://api.crossref.org/works/{doi}")
    r.raise_for_status()
    m = r.json()
    assert "status" in m and m["status"] == "ok", m
    return m["message"]


def fetch_publications(mongo=None):
    from pymongo import MongoClient

    if mongo is None:
        mongo = "mongodb://127.0.0.1:27017/personnel"

    c = MongoClient(mongo)
    db = c.get_default_database()
    pubsl = list(
        db.publications.find({}, {"doi": 1, "pubmed": 1, "title": 1, "year": 1})
    )

    pubs = pd.DataFrame.from_records(pubsl)
    pubs.year = pubs.year.astype(int)
    pubs = pubs.sort_values(["year", "title"])
    pubs = pubs.drop("_id", axis="columns")
    pubs["ncitations"] = -1
    return pubs


def citations(doi):
    r = fetch_opennet(doi)
    for d in r:
        m = DOI.match(d["cited"])
        if m and m.group(1) != doi:
            continue
        m = DOI.match(d["citing"])
        if m:
            yield m.group(1)


def citation_df(doi):
    df = pd.DataFrame({"citedby": list(citations(doi))})
    df["doi"] = doi
    return df


class Db:
    def __init__(self, engine, publications, citations_):
        from sqlalchemy import bindparam, select

        self.engine = engine
        self.publications = publications
        self.citations = citations_
        self.select = select
        self.update = (
            publications.update()  # pylint: disable=no-value-for-parameter
            .values({publications.c.ncitations: bindparam("b_ncitations")})
            .where(publications.c.doi == bindparam("b_doi"))
        )

    def count(self, t, q=None):
        from sqlalchemy import func

        q2 = self.select([func.count()]).select_from(t)
        if q:
            q2 = q2.where(q)
        with self.engine.connect() as conn:
            return conn.execute(q2).fetchone()[0]

    def update_citation_count(self, doi, ncitations):
        with self.engine.connect() as con:
            proxy = con.execute(self.update, b_doi=doi, b_ncitations=ncitations)
            assert proxy.rowcount == 1, (doi, proxy.rowcount)

    def update_citations(self, df):
        df.to_sql("citations", con=self.engine, if_exists="append", index=False)

    def todo(self):

        return pd.read_sql_query(
            self.select([self.publications]).where(self.publications.c.ncitations < 0),
            con=self.engine,
        )

    def npubs(self):
        return self.count(self.publications)

    def ndone(self):
        return self.count(self.publications, q=self.publications.c.ncitations >= 0)

    def ncitations(self):
        return self.count(self.citations)


def initdb():
    from sqlalchemy import (
        create_engine,
        MetaData,
        String,
        Table,
        Integer,
        Column,
    )

    meta = MetaData()
    Publications = Table(
        "publications",
        meta,
        Column("id", Integer, primary_key=True),
        Column("doi", String(64), index=True),
        Column("pubmed", String(16)),
        Column("title", String(256)),
        Column("year", Integer),
        Column("ncitations", Integer),
    )

    Citations = Table(
        "citations",
        meta,
        Column("id", Integer, primary_key=True),
        Column("doi", String(64), index=True),
        Column("citedby", String(64)),
    )

    engine = create_engine("sqlite:///./citations.db")
    Publications.create(bind=engine, checkfirst=True)
    Citations.create(bind=engine, checkfirst=True)

    return Db(engine, Publications, Citations)


def docitations(db: Db, sleep=1.0):
    from tqdm import tqdm
    from requests.exceptions import HTTPError

    todo = db.todo()
    ncitations = db.ncitations()
    click.secho(f"todo: {len(todo)}. Already found {ncitations} citations", fg="yellow")
    added = 0
    mx_exc = 4
    with tqdm(todo.iterrows(), total=len(todo), postfix={"added": 0}) as pbar:
        for idx, row in pbar:
            if not row.doi:
                pbar.write(click.style(f"{idx}: no DOI", fg="red"))
                continue
            try:
                df = citation_df(row.doi)
                db.update_citation_count(row.doi, len(df))
                db.update_citations(df)
                added += len(df)
                pbar.set_postfix(added=added)
                if sleep:
                    time.sleep(sleep)
            except HTTPError as e:
                mx_exc -= 1
                if mx_exc <= 0:
                    raise e
                pbar.write(click.style(f"{row.doi}: exception {e}", fg="red"))


@cli.command(name="citations")
@click.option("--sleep", default=1.0)
@click.option("--mongo")
def citations_(sleep, mongo):
    db = initdb()
    if db.npubs() == 0:
        pubs = fetch_publications(mongo)
        click.secho(f"found {len(pubs)} publications", fg="green")
        pubs.to_sql(  # pylint: disable=no-member
            "publications", con=db.engine, if_exists="append", index=False
        )

    docitations(db, sleep)


if __name__ == "__main__":
    citations_()  # pylint: disable=no-value-for-parameter
