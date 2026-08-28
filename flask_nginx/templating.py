from __future__ import annotations

import functools
import os
from os.path import join
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .utils import topath

if TYPE_CHECKING:
    from jinja2 import Environment, Template, UndefinedError


def templates_dir() -> Path:
    return Path(__file__).parent / "templates"


def get_template_filename(name: str) -> Path:
    return templates_dir() / name


@functools.lru_cache
def get_env(application_dir: Path | None = None) -> Environment:  # noqa: C901
    import datetime

    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

    def ujoin(*args: Any) -> str:  # noqa: ANN401
        for path in args:
            if isinstance(path, StrictUndefined):
                msg = "undefined argument to join"
                raise UndefinedError(msg)
        return join(*[str(s) for s in args])  # noqa: PTH118

    def split(
        s: str | StrictUndefined,
        sep: str | None = None,
    ) -> list[str] | StrictUndefined:
        if isinstance(s, StrictUndefined):
            return s
        return s.split(sep)

    def envf(envvar: str, default: str | None = None) -> str:
        if isinstance(envvar, StrictUndefined):
            msg = "undefined argument to env"
            raise UndefinedError(msg)
        ret = os.environ.get(envvar, default)
        if ret is not None:
            return ret
        msg = f"unknown environment variable: {envvar}"
        raise UndefinedError(msg)

    def normpath(path: str | StrictUndefined) -> str | StrictUndefined:
        if isinstance(path, StrictUndefined):
            return path
        return str(topath(path))

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
        "env": envf,  # e.g. {{ 'MAMBA_ROOT_PREFIX'|env }}
    }

    glb: dict[str, Any] = {
        "join": ujoin,
        # "cmd": " ".join(sys.argv), # noqa: ERA001
        "now": lambda: datetime.datetime.now(datetime.timezone.utc),
    }

    templates = [templates_dir()]
    if application_dir:
        templates = [application_dir, *templates]
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader(templates), autoescape=False)  # noqa: S701
    env.filters.update(filt)
    env.globals.update(glb)

    return env


def get_template(
    template: str | Path | Template,
    application_dir: Path | None = None,
) -> Template:
    from jinja2 import Template

    if isinstance(template, Template):
        return template
    template = Path(template)
    env = get_env(application_dir)
    if template.is_absolute() and template.is_file():
        with template.open(encoding="utf8") as fp:
            t = env.from_string(fp.read())
            t.filename = str(template)
            return t
    return env.get_template(str(template))


def get_templates(template: str) -> list[str | Template]:

    templates: list[str | Template]

    tm = topath(template)
    if tm.is_dir():
        env = get_env(tm)
        templates = [env.get_template(f.name) for f in sorted(tm.iterdir())]
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
            f" variable{s} in template: {' '.join(missing)}",
            fg="yellow",
        )
    else:
        mtext = ""
    msg = click.style(f"{msg}:{mtext}")
    click.secho(msg, err=True)
