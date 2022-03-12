from __future__ import annotations

from os.path import abspath, dirname, expanduser, join, normpath

from jinja2 import Environment, Template, UndefinedError


def topath(path: str) -> str:
    return normpath(abspath(expanduser(path)))


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
                raise UndefinedError("undefined argument")
        return join(*args)

    templates = [templates_dir()]
    if application_dir:
        templates = [application_dir, *templates]
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader(templates))
    env.filters["normpath"] = topath
    env.globals["join"] = ujoin
    env.globals["cmd"] = " ".join(sys.argv)
    env.globals["now"] = datetime.datetime.utcnow
    return env


def get_template(
    template: str | Template, application_dir: str | None = None
) -> Template:
    if isinstance(template, Template):
        return template
    return get_env(application_dir).get_template(template)
