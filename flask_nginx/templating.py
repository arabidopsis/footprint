from __future__ import annotations

from os.path import dirname
from os.path import isabs
from os.path import join
from typing import Any

import click
import jinja2
from jinja2 import Environment
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

    def ujoin(*args: Any) -> str:
        for path in args:
            if isinstance(path, StrictUndefined):
                raise UndefinedError("undefined argument to join")
        return join(*[str(s) for s in args])

    def split(
        s: str | StrictUndefined,
        sep: str | None = None,
    ) -> list[str] | StrictUndefined:
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

    def maybe_colon(s: str | StrictUndefined) -> str:
        if isinstance(s, StrictUndefined):
            return ""
        if not s:
            return s
        if s.endswith(":"):
            return s
        return s + ":"

    filt: dict[str, Any] = {
        "normpath": normpath,
        "split": split,
        "maybe_colon": maybe_colon,
    }

    glb: dict[str, Any] = {
        "join": ujoin,
        "cmd": " ".join(sys.argv),
        "now": lambda: datetime.datetime.now(datetime.timezone.utc),
    }

    env.filters.update(filt)  # type: ignore
    env.globals.update(glb)  # type: ignore

    return env


def get_template(
    template: str | jinja2.Template,
    application_dir: str | None = None,
) -> jinja2.Template:
    if isinstance(template, jinja2.Template):
        return template
    env = get_env(application_dir)
    if isabs(template):
        with open(template, encoding="utf8") as fp:
            return env.from_string(fp.read())
    return env.get_template(template)


def get_templates(template: str) -> list[str | jinja2.Template]:
    import os

    templates: list[str | jinja2.Template]

    tm = topath(template)
    if os.path.isdir(tm):
        env = get_env(tm)
        templates = [env.get_template(f) for f in sorted(os.listdir(tm))]
    else:
        templates = [template]

    return templates


def undefined_error(
    exc: UndefinedError,
    template: jinja2.Template,
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
