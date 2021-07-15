from os.path import abspath, dirname, join, normpath
import typing as t
from jinja2 import UndefinedError, Template, Environment


def topath(path: str) -> str:
    return normpath(abspath(path))

def get_env(application_dir: t.Optional[str]=None) -> Environment:
    import datetime
    import sys

    from jinja2 import FileSystemLoader, StrictUndefined

    def ujoin(*args) -> str:
        for path in args:
            if isinstance(path, StrictUndefined):
                raise UndefinedError("undefined argument")
        return join(*args)

    templates = [join(dirname(__file__), "templates")]
    if application_dir:
        templates = [application_dir] + templates
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader(templates))
    env.filters["normpath"] = topath
    env.globals["join"] = ujoin
    env.globals["cmd"] = " ".join(sys.argv)
    env.globals["now"] = datetime.datetime.utcnow
    return env

def get_template(template: str, application_dir: t.Optional[str]=None) -> Template:
    return get_env(application_dir).get_template(template)