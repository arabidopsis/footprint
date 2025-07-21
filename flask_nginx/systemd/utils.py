from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from os.path import isdir
from os.path import isfile
from os.path import join
from pathlib import Path
from typing import Any
from typing import Callable
from typing import TextIO
from typing import TypeVar

import click

from ..core import get_dot_env
from ..core import StaticFolder

F = TypeVar("F", bound=Callable[..., Any])

NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

CONVERTER = Callable[[dict[str, Any]], Any]

CHECKTYPE = Callable[[str, Any], str | None]


class ArgError(Exception):
    def __init__(self, message: str):
        super().__init__()
        self.message = message


def fix_kv(
    key: str,
    values: list[str],
    convert: dict[str, CONVERTER] | None = None,
) -> tuple[str, Any]:
    # if key in {"gevent"}:  # boolean flag
    #     return ("gevent", True)
    if "" in values:
        raise ArgError(f"no value for {key}")
    key = key.replace("-", "_")
    if not values:  # simple key is True
        return (key, True)
    value = "=".join(values)

    def get_value(value: str) -> tuple[str, Any]:
        if key in {"user"}:  # user is a string!
            return (key, value)
        if value.isdigit():
            return (key, int(value))
        if value == "true":
            return (key, True)
        if value == "false":
            return (key, False)
        if NUM.match(value):
            return (key, float(value))
        return (key, value)

    key, v = get_value(value)
    if convert and key in convert:
        v = convert[key](v)
    return key, v


def fix_params(
    params: list[str],
    convert: dict[str, CONVERTER] | None = None,
) -> dict[str, Any]:
    from jinja2 import UndefinedError, Undefined

    def f(p: str) -> tuple[str, Any]:
        k, *values = p.split("=")
        if values == [""]:  # just skip 'key=' mistakes
            return k, Undefined
        return fix_kv(k, values, convert)

    try:
        return dict(f(p) for p in params)
    except ArgError as e:
        raise UndefinedError(e.message) from e


# KW = re.compile(r"^([\w_-]+)\s*:", re.M)


def get_known(help_args: dict[str, str]) -> set[str]:
    return {s.replace("-", "_") for s in help_args}


def url_match(directory: str, exclude: Sequence[str] | None = None) -> str:
    # scan directory and add any extra files directories
    # that are needed for location ~ /^(match1|match2|...) { .... }

    from ..config import get_config

    Config = get_config()

    if exclude is not None:
        sexclude = set(Config.exclude) | set(exclude)
    else:
        sexclude = set(Config.exclude)

    dirs = set(Config.static_dir)
    files = set(Config.static_files)
    for f in os.listdir(directory):
        if f in sexclude:
            continue
        tl = dirs if isdir(join(directory, f)) else files
        tl.add(f)

    d = "|".join(f.replace(".", r"\.") for f in dirs)
    f = "|".join(f.replace(".", r"\.") for f in files)
    return f"(^/({d})/|^({f})$)"


def find_favicon(application_dir: str) -> str | None:
    """Find directory with favicon.ico or robot.txt or other toplevel files"""
    from ..config import get_config

    Config = get_config()

    static = set(Config.static_files)
    for d, dirs, files in os.walk(application_dir, topdown=True):
        dirs[:] = [f for f in dirs if not f.startswith((".", "_"))]
        if d.startswith((".", "_")):
            continue
        for f in files:
            if f in static:
                return d
    return None


def check_app_dir(application_dir: str) -> str | None:
    if not isdir(application_dir):
        return f"not a directory: {application_dir}"
    return None


def check_venv_dir(venv_dir: str) -> str | None:
    if not isdir(venv_dir):
        return "venv: not a directory: {venv_dir}"

    py = join(venv_dir, "bin", "python")
    if not os.access(py, os.X_OK | os.R_OK):
        return f"venv: {venv_dir} does not have python installed!"
    return None


def footprint_config(application_dir: str) -> dict[str, Any]:
    def dot_env(f: str) -> dict[str, Any]:
        cfg = get_dot_env(f)
        if cfg is None:
            return {}
        return dict(
            fix_kv(k.lower(), [v])
            for k, v in cfg.items()
            if k.isupper() and v is not None
        )

    f = join(application_dir, ".flaskenv")
    if not isfile(f):
        return {}
    return dot_env(f)


def get_default_venv() -> str:
    venv = Path(sys.executable).parent.parent
    return str(venv)


def has_error_page(static_folders: list[StaticFolder]) -> StaticFolder | None:
    for s in static_folders:
        if "404.html" in os.listdir(s.folder):
            return s
    return None


def fixname(n: str) -> str:
    # return n.replace("\\", "\\\\")
    return n


def getgroup(username: str) -> str | None:
    username = username.replace("\\\\", "\\")
    try:
        # username might not exist on this machine
        ret = subprocess.check_output(["id", "-gn", username], text=True).strip()
        return fixname(ret)
    except subprocess.CalledProcessError:
        return None


def getuser() -> str | None:
    try:
        # username might not exist on this machine
        ret = subprocess.check_output(["id", "-un"], text=True).strip()
        return fixname(ret)
    except subprocess.CalledProcessError:
        return None


def make_args(argsd: dict[str, str], **kwargs: Any) -> str:
    from itertools import chain

    from ..config import get_config

    Config = get_config()

    def color(s: str) -> str:
        if Config.arg_color == "none":
            return s
        return click.style(s, fg=Config.arg_color)

    args = list((k, v) for k, v in chain(argsd.items(), kwargs.items()))

    argl = [(color(k), v) for k, v in args]
    aw = len(max(argl, key=lambda t: len(t[0]))[0]) + 1
    bw = len(max(args, key=lambda t: len(t[0]))[0]) + 1
    sep = "\n  " + (" " * bw)

    def fixd(d: str) -> str:
        dl = d.split("\n")
        return sep.join(dl)

    return "\n".join(f"{arg:<{aw}}: {fixd(desc)}" for arg, desc in argl)


def to_check_func(
    key: str,
    func: Callable[[Any], bool],
    msg: str,
) -> tuple[str, CHECKTYPE]:
    def f(k: str, val: Any) -> str | None:
        if func(val):
            return None
        return msg.format(**{key: val})

    return (key, f)


def to_output(res: str, output: str | TextIO | None = None) -> None:
    if not res.endswith("\n"):
        res += "\n"
    if output:
        if isinstance(output, str):
            with open(output, "w", encoding="utf-8") as fp:
                fp.write(res)

        else:
            output.write(res)
    else:
        click.echo(res)


def config_options(f: F) -> F:
    f = click.option(
        "-o",
        "--output",
        help="write to this file",
        type=click.Path(dir_okay=False),
    )(f)
    f = click.option("-n", "--no-check", is_flag=True, help="don't check parameters")(f)
    return f


# def su(f):
#     return click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")(
#         f,
#     )


def asuser_option(f: F) -> F:
    f = click.option("-u", "--user", "asuser", is_flag=True, help="Install as user")(f)
    return f


def check_user(asuser: bool) -> None:
    if asuser:
        if os.geteuid() == 0:
            raise click.BadParameter(
                "can't install to user if running as root",
                param_hint="user",
            )


def template_option(f: F) -> F:
    return click.option(
        "-t",
        "--template",
        metavar="TEMPLATE_FILE",
        help="template file or directory of templates",
    )(f)


def asgi_option(f: F) -> F:
    return click.option(
        "--asgi",
        is_flag=True,
        help="run as asyncio (Quart|FastAPI)",
    )(f)
