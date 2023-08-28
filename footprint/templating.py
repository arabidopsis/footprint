from __future__ import annotations

from os.path import dirname
from os.path import join
from typing import Any

import click
from jinja2 import Environment
from jinja2 import Template
from jinja2 import UndefinedError

from .core import topath


def templates_dir() -> str:
    return join(dirname(__file__), "templates")


def get_template_filename(name: str) -> str:
    return join(templates_dir(), name)


def get_env(application_dir: str | None = None) -> Environment:
    import datetime
    import sys

    from jinja2 import FileSystemLoader, StrictUndefined

    def ujoin(*args) -> str:
        for path in args:
            if isinstance(path, StrictUndefined):
                raise UndefinedError("undefined argument to join")
        return join(*args)

    def split(s: str | StrictUndefined, sep=None) -> list[str] | StrictUndefined:
        if isinstance(s, StrictUndefined):
            # raise UndefinedError("undefined argument to split")
            return s
        if sep is None:
            return s.split()
        return s.split(sep)

    def normpath(path: str | StrictUndefined) -> str | StrictUndefined:
        if isinstance(path, StrictUndefined):
            # raise UndefinedError("undefined argument to normpath")
            return path
        return topath(path)

    templates = [templates_dir()]
    if application_dir:
        templates = [application_dir, *templates]
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader(templates))

    env.filters["normpath"] = normpath
    env.filters["split"] = split
    env.globals["join"] = ujoin
    env.globals["cmd"] = " ".join(sys.argv)
    env.globals["now"] = datetime.datetime.utcnow
    return env


def get_template(
    template: str | Template,
    application_dir: str | None = None,
) -> Template:
    if isinstance(template, Template):
        return template
    return get_env(application_dir).get_template(template)


def get_templates(template: str) -> list[str | Template]:
    import os

    templates: list[str | Template]

    tm = topath(template)
    if os.path.isdir(tm):
        env = get_env(tm)
        templates = [env.get_template(f) for f in sorted(os.listdir(tm))]
    else:
        templates = [template]

    return templates


def undefined_error(
    exc: UndefinedError,
    template: Template,
    params: dict[str, Any],
) -> None:
    from .utils import get_variables

    msg = click.style(f"{exc.message}", fg="red", bold=True)
    names = sorted(params)
    variables = get_variables(template)
    missing = variables - set(names)
    if missing:
        s = "s" if len(missing) > 1 else ""
        mtext = click.style(
            f' variable{s} in template: {" ".join(missing)}',
            fg="yellow",
        )
    else:
        mtext = ""
    msg = click.style(f"{msg}:{mtext}")
    click.secho(msg, err=True)
