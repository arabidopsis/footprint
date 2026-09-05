from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from shutil import which
from typing import IO, TYPE_CHECKING, Any, TypeVar, cast

import click

from ..utils import get_dot_env, toml_load

if TYPE_CHECKING:
    from ..core import StaticFolder

F = TypeVar("F", bound=Callable[..., Any])

NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

# e.g. func(params, value) -> Any
CONVERTER = Callable[[dict[str, Any]], Any]

CHECKTYPE = Callable[[str, Any], str | None]


def fix_kv(
    key: str,
    value: str,
    convert: dict[str, Callable[[str], Any]] | None = None,
) -> tuple[str, Any]:

    def get_value(value: str) -> tuple[str, Any]:
        if key == "user":  # user is a string!
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

    if convert and key in convert:
        return (key, convert[key](value))
    return get_value(value)


def fix_params(
    params: list[str],
    convert: dict[str, Callable[[Any], Any]] | None = None,
) -> dict[str, Any]:
    from jinja2 import Undefined

    def f(p: str) -> tuple[str, Any]:
        k, *v = p.split("=", maxsplit=1)
        k = k.lstrip("-").replace("-", "_")
        if not v:
            return k, True
        value = v[0]
        if not value:  # just skip 'key=' mistakes
            return k, Undefined
        return fix_kv(k, value, convert)

    return dict(f(p) for p in params)


def get_known(help_args: dict[str, str]) -> set[str]:
    return {s.replace("-", "_") for s in help_args}


def url_match(directory: str | Path, exclude: Sequence[str] | None = None) -> str:
    # scan directory and add any extra files directories
    # that are needed for location ~ /^(match1|match2|...) { .... }

    from ..config import get_config

    config = get_config()

    sexclude = set(config.exclude)
    if exclude is not None:
        sexclude.update(exclude)

    directory = Path(directory)

    dirs = set(config.static_dir)
    files = set(config.top_level_files)
    for file in directory.iterdir():
        if file.name in sexclude:
            continue
        tl = dirs if file.is_dir() else files
        tl.add(file.name)

    d = "|".join(re.escape(f) for f in dirs)
    f = "|".join(re.escape(f) for f in files)
    return f"(^/({d})/|^({f})$)"


def find_toplevel(application_dir: str) -> str | None:
    """Find directory with favicon.ico or robot.txt or other toplevel files (first one found is returned)."""
    from ..config import get_config

    config = get_config()

    static = set(config.top_level_files)
    for d, dirs, files in os.walk(application_dir, topdown=True):
        dirs[:] = [f for f in dirs if not f.startswith((".", "_"))]
        if d.startswith((".", "_")):
            continue
        for f in files:
            if f in static:
                return d
    return None


def check_app_dir(application_dir: str | Path) -> str | None:
    application_dir = Path(application_dir)
    if not application_dir.is_dir():
        return f"not a directory: {application_dir}"
    return None


def check_venv_dir(venv_dir: str | Path) -> str | None:
    venv_dir = Path(venv_dir)
    if not venv_dir.is_dir():
        return f"venv: not a directory: {venv_dir}"

    py = venv_dir / "bin" / "python"
    if not py.exists() or not os.access(py, os.X_OK | os.R_OK):
        return f"venv: {venv_dir} does not have python installed!"
    return None


def has_error_page(
    static_folders: list[StaticFolder],
    error_pages: list[int] | None = None,
) -> Iterator[tuple[StaticFolder, int]]:
    if error_pages is None:
        error_pages = [404]
    for s in static_folders:
        files = [f.name for f in s.directory.iterdir()]
        for ep in error_pages:
            if f"{ep}.html" in files:
                yield (s, ep)


def fixname(n: str) -> str:
    return n


def getgroup(username: str) -> str | None:
    username = username.replace("\\\\", "\\")
    try:
        # username might not exist on this machine
        idcmd = which("id")
        if idcmd is None:
            return None
        ret = subprocess.check_output([idcmd, "-gn", username], text=True).strip()
        return fixname(ret)
    except subprocess.CalledProcessError:
        return None


def getuser() -> str | None:
    try:
        # username might not exist on this machine
        idcmd = which("id")
        if idcmd is None:
            return None
        ret = subprocess.check_output([idcmd, "-un"], text=True).strip()
        return fixname(ret)
    except subprocess.CalledProcessError:
        return None


def make_args(argsd: dict[str, str], **kwargs: Any) -> str:  # noqa: ANN401
    from itertools import chain

    from ..config import get_config

    config = get_config()

    def color(s: str) -> str:
        if config.arg_color == "none":
            return s
        return click.style(s, fg=config.arg_color)

    args = [(k, v) for k, v in chain(argsd.items(), kwargs.items())]

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
    def f(_k: str, val: Any) -> str | None:  # noqa: ANN401
        if func(val):
            return None
        return msg.format(**{key: val})

    return (key, f)


def to_output(res: str, output: str | IO[str] | Path | None = None) -> None:
    if not res.endswith("\n"):
        res += "\n"
    if output:
        if isinstance(output, (str, Path)):
            with Path(output).open("w", encoding="utf-8") as fp:
                fp.write(res)

        else:
            output.write(res)
    else:
        click.echo(res)


def config_options(f: F) -> F:
    f = click.option(
        "-o",
        "--output",
        help="write to this file [default: stdout]",
        type=click.Path(dir_okay=False),
    )(f)
    return click.option("-n", "--no-check", is_flag=True, help="don't check parameter values")(f)


def asuser_option(f: F) -> F:
    return click.option(
        "-u",
        "--user",
        "asuser",
        is_flag=True,
        help="select systemd --user location to install/uninstall (e.g. ~/.config/systemd/user)",
    )(f)


def ignore_unknowns_option(f: F) -> F:
    return click.option(
        "-i", "--ignore-unknowns", is_flag=True, help="ignore any unused/unknown parameters in the template"
    )(f)


def webserver_option(f: F) -> F:
    return click.option(
        "-s",
        "--server",
        type=click.Choice(["gunicorn", "granian", "uvicorn", "hypercorn"], case_sensitive=False),
        help="use the specified module to serve the application (default: auto-detect from Python environment)",
    )(f)


def python_executable_option(f: F) -> F:
    return click.option(
        "--python-executable",
        metavar="EXECUTABLE",
        type=str,
        help="path to workspace's Python executable",
    )(f)


def template_option(f: F) -> F:
    return click.option(
        "-t",
        "--template",
        metavar="TEMPLATE_FILE",
        help="template file. If not specified, the default template will be used.",
    )(f)


def app_dir_option(f: F) -> F:
    return click.option(
        "-d",
        "--app-dir",
        "application_dir",
        type=click.Path(exists=True, dir_okay=True, file_okay=False, path_type=Path),
        help="""location of repo [default: current directory]""",
    )(f)


def asgi_option(f: F) -> F:
    return click.option(
        "--asgi",
        is_flag=True,
        help="run as asyncio (Quart|FastAPI)",
    )(f)


def check_user(*, asuser: bool) -> None:
    if asuser and os.geteuid() == 0:
        msg = "can't install to user if running as root"
        raise click.BadParameter(
            msg,
            param_hint="user",
        )


def verify(entrypoint: str, application_dir: Path, *, python_executable: str | None = None) -> None:
    from ..core import introspect

    introspect(application_dir, entrypoint, python_executable=python_executable)


def find_webserver(*, asgi: bool, python_executable: str | None) -> str:
    """Find the appropriate webserver module for the given Python executable."""
    from ..utils import has_mod

    asgi_servers = ["gunicorn", "granian", "uvicorn", "hypercorn"]
    wsgi_servers = ["gunicorn", "granian"]

    if asgi:
        for mod in asgi_servers:
            if has_mod(mod, python_executable):
                return mod
    else:
        for mod in wsgi_servers:
            if has_mod(mod, python_executable):
                return mod
    pkg = " or ".join(asgi_servers if asgi else wsgi_servers)
    msg = f"""no {pkg} package found in {python_executable or sys.executable}. Either:
    1. install {pkg}
    2. check or uncheck the --asgi option
    3. specify another python environment with --python-executable.
"""
    click.secho(msg, err=True, fg="red")
    raise click.Abort


def get_app_entrypoint(
    application_dir: Path,
    *,
    default: str = "main:app",
) -> str:
    """Get entrypoint for app from environment variables or .env files."""
    envs = ["QUART_APP", "FASTAPI_APP", "UVICORN_APP", "FLASK_APP"]
    dotenvs = [".quartenv", ".fastapienv", ".flaskenv", ".env"]

    for e in envs:
        app = os.environ.get(e)
        if app is not None:
            if ":" not in app:
                app += ":application"
            return app
    for dotenv in dotenvs:
        dot = application_dir / dotenv
        if dot.is_file():
            cfg = get_dot_env(dot)
            if cfg is None:
                continue
            for e in envs:
                app = cfg.get(e)
                if app is not None:
                    if ":" not in app:
                        app += ":application"
                    return app
    return get_project_entrypoint(application_dir, default=default)


def get_project_entrypoint(
    application_dir: Path,
    *,
    default: str = "main:app",
) -> str:
    """Get entrypoint for app from pyproject.toml files."""
    project_toml = application_dir / "pyproject.toml"
    if project_toml.is_file():
        cfg = toml_load(project_toml)
        tool_cfg = cfg.get("tool", {})
        for tool in ("fastapi", "starlette", "quart", "flask"):
            if tool in tool_cfg:
                cfg = tool_cfg.get(tool, {})
                app = cfg.get("entrypoint")
                if app is not None:
                    if ":" not in app:
                        app += ":application"
                    return str(app)
    return default


def footprint_config(application_dir: Path, ext: str | None) -> dict[str, Any]:
    """Load parameters from pyproject.toml under [tool.footprint.{ext}]."""
    if ext is None:
        return {}
    project_toml = application_dir / "pyproject.toml"
    if not project_toml.is_file():
        return {}

    cfg = toml_load(project_toml)
    tool_cfg = cfg.get("tool", {})
    if not tool_cfg:
        return {}
    fp_cfg = tool_cfg.get("footprint", {})

    return cast("dict[str, Any]", fp_cfg.get(ext, {}))
