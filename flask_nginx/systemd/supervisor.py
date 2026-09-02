from __future__ import annotations

import sys
from os.path import isdir, join
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import click

from .cli import config
from .utils import (
    CHECKTYPE,
    CONVERTER,
    app_dir_option,
    asuser_option,
    config_options,
    make_args,
    python_executable_option,
    template_option,
)

if TYPE_CHECKING:
    from jinja2 import Template

SUPERVISORD_ARGS = {
    "application_dir": "locations of all repo",
    "appname": "application name [default: directory name]",
    "annotator": "annotator repo directory",
    "venv": "virtual env directory [default: where python executable exists]",
    "user": "user to run as [default: current user]",
    "group": "group to run as [default: current user group]",
    "workers": "number of julia and celery workers to start [default: 4]",
    "threads": "number of julia threads to use [default: 8]",
    "stopwait": "seconds to wait for julia and celery to stop [default: 30]",
    "heatbeat": "celery worker heatbeat interval in seconds [default: 30]",
    "gevent": "run celery worker with gevent `-P gevent`",
    "max_interval": "interval between beats [default: 3600]",
    "after": "start after this service [default: mysql.service]",
    "celery": "celery --app to start [default: {appname}.celery]",
    "julia": "julia directory",
    "depot_path": "where downloaded julia packages are stored [default: ~{user}/.julia ]",
}
SUPERVISORD_HELP = f"""
Generate a supervisord conf file for website background.

Use footprint config supervisord ... etc.
with the following params:

\b
{make_args(SUPERVISORD_ARGS)}
\b
example:
\b
footprint config supervisord venv=/home/ianc/miniconda3
"""
CELERY_ARGS = {
    "after": "start after this service [default: network-online.target]",
    "application_dir": "locations of repo [default: current directory]",
    "appname": "application name [default: directory name]",
    "celery": "celery --app to start [default: {appname}.celery]",
    "gevent": "run celery worker with gevent `-P gevent`",
    "group": "group to run as [default: current user group]",
    "path": "path(s) to add to PATH environment variable",
    "user": "user to run as [default: current user]",
    "stopwait": "seconds to wait for julia and celery to stop [default: 30]",
    "venv": "virtual env directory [default: where python executable exists]",
    "workers": "number of celery workers to start [default: half of cpu count]",
}

CELERY_SYSTEMD_HELP = f"""
Generate a systemd conf file for website background.

Use footprint config systemd-celery ... etc.
with the following params:

\b
{make_args(CELERY_ARGS)}
\b
example:
\b
footprint config systemd-celery workers=4 after=mysql.service
"""


def supervisor(
    template: str | Path | Template,
    application_dir: Path | None = None,
    args: list[str] | None = None,
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | IO[str] | Path | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
    default_values: list[tuple[str, CONVERTER]] | None = None,
) -> str:
    import os

    from ..utils import topath
    from .systemd import systemd

    def isadir(key: str, s: Any) -> str | None:  # noqa: ANN401
        if not isdir(s):  # noqa: PTH112
            return f"{key}: {s} is not a directory"
        return None

    def is_julia(key: str, s: Any) -> str | None:  # noqa: ANN401
        if not isdir(s):  # noqa: PTH112
            return f"{key}: {s} is not a directory"
        if not os.access(join(s, "bin", "julia"), os.X_OK | os.R_OK):  # noqa: PTH118
            return f"{key}: {s} is not a *julia* directory"
        return None

    schecks: list[tuple[str, CHECKTYPE]] = [
        ("julia_dir", is_julia),
        ("depot_path", isadir),
    ]
    schecks.extend(checks or [])

    defaults: list[tuple[str, CONVERTER]] = [
        ("depot_path", lambda params: f"{params['homedir']}/.julia"),
        ("workers", lambda _: 4),
        ("gevent", lambda _: False),
        ("stopwait", lambda _: 10),
    ]
    if default_values:
        defaults = [*default_values, *defaults]

    return systemd(
        template,
        application_dir or Path.cwd(),
        args,
        help_args=help_args or SUPERVISORD_ARGS,
        check=check,
        output=output,
        asuser=asuser,
        extra_params=extra_params,
        default_values=defaults,
        ignore_unknowns=ignore_unknowns,
        checks=schecks,
        convert={"julia_dir": topath, "depot_path": topath},
    )


def supervisord(
    template: str | None,
    application_dir: Path | None,
    args: list[str],
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | Path | IO[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
) -> None:
    from ..templating import get_templates
    from ..utils import maybe_closing, rmfiles

    templates = get_templates(template or "supervisor.ini")
    application_dir = application_dir or Path.cwd()

    with maybe_closing(
        Path(output).open("w", encoding="utf-8") if isinstance(output, (str, Path)) else output,
    ) as fp:
        try:
            for tplt in templates:
                supervisor(
                    tplt,
                    application_dir,
                    args,
                    check=check,
                    output=fp,
                    ignore_unknowns=ignore_unknowns,
                    help_args=help_args,
                    extra_params=extra_params,
                    checks=checks,
                    asuser=asuser,
                )
        except Exception:
            if isinstance(output, (str, Path)):
                rmfiles([str(output)])
            raise


@config.command(name="supervisord", help=SUPERVISORD_HELP, hidden=True)
@config_options
@template_option
@app_dir_option
@click.argument("params", nargs=-1, required=False)
def supervisord_cmd(
    application_dir: Path | None,
    params: list[str],
    template: str | None,
    output: str | None,
    *,
    no_check: bool,
) -> None:
    supervisord(
        template,
        application_dir or Path.cwd(),
        params,
        check=not no_check,
        output=output,
        ignore_unknowns=True,
    )


@config.command(name="systemd-celery", help=CELERY_SYSTEMD_HELP)
@template_option
@asuser_option
@config_options
@python_executable_option
@app_dir_option
@click.option("-c", "--celery", "celery_mod", help="celery --app to start [default: search...]")
@click.argument("params", nargs=-1, required=False)
def systemd_celery_cmd(
    application_dir: Path | None,
    params: list[str],
    template: str | None,
    output: str | None,
    python_executable: str | None = None,
    *,
    celery_mod: str | None = None,
    no_check: bool,
    asuser: bool,
) -> None:

    from ..utils import has_mod
    from .systemd import systemd

    def find_celery() -> str | None:
        for fd in (application_dir or Path.cwd()).iterdir():
            if fd.is_dir():
                for mod in ["celery", "tasks"]:
                    if (fd / f"{mod}.py").is_file():
                        return f"{fd.name}.{mod}"
        return None

    if not has_mod("celery", python_executable=python_executable):
        click.secho(f"celery is not installed in {python_executable or sys.executable}!", fg="red", bold=True)
        raise click.Abort

    celery = celery_mod or find_celery()
    if celery is None:
        click.secho(f"celery app not found in {application_dir}! Use --celery to specify the app.", fg="red", bold=True)
        raise click.Abort
    extra_params = {"celery": celery}
    systemd(
        template or "celery.service",
        application_dir,
        params,
        help_args=CELERY_ARGS,
        check=not no_check,
        extra_params=extra_params,
        output=output,
        asuser=asuser,
        python_executable=python_executable,
    )
