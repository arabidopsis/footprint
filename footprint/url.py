from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import unquote


@dataclass
class URL:
    drivername: str
    username: str | None = None
    password: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    query: dict[str, str | list[str] | None] | None = None


# direct ripoff of sqlalchemy.engine.url:make_url
# https://github.com/sqlalchemy/sqlalchemy/blob/7fdaf711dc6bd578f7becf45526dce70f523890d/lib/sqlalchemy/engine/url.py#L821


def make_url(name_or_url: str | URL) -> URL | None:
    if isinstance(name_or_url, URL):
        return name_or_url
    pattern = re.compile(
        r"""
            (?P<name>[\w\+]+)://
            (?:
                (?P<username>[^:/]*)
                (?::(?P<password>[^@]*))?
            @)?
            (?:
                (?:
                    \[(?P<ipv6host>[^/\?]+)\] |
                    (?P<ipv4host>[^/:\?]+)
                )?
                (?::(?P<port>[^/\?]*))?
            )?
            (?:/(?P<database>[^\?]*))?
            (?:\?(?P<query>.*))?
            """,
        re.X,
    )

    m = pattern.match(name_or_url)
    if m is None:
        return None

    components: dict[str, Any] = m.groupdict()
    if components["query"] is not None:
        query: dict[str, str | list[str]] = {}

        for key, value in parse_qsl(components["query"]):
            if key in query:
                query[key] = v = to_list(query[key])
                v.append(value)
            else:
                query[key] = value
    else:
        query = None  # type: ignore
    components["query"] = query

    if components["username"] is not None:
        components["username"] = unquote(components["username"])

    if components["password"] is not None:
        components["password"] = unquote(components["password"])

    ipv4host = components.pop("ipv4host")
    ipv6host = components.pop("ipv6host")
    components["host"] = ipv4host or ipv6host
    name = components.pop("name")

    if components["port"]:
        components["port"] = int(components["port"])

    return URL(drivername=name, **components)


def to_list(x: str | list[str]) -> list[str]:
    if not isinstance(x, Iterable) or isinstance(x, (str, bytes)):
        return [str(x)]
    elif isinstance(x, list):
        return x
    else:
        return list(x)
