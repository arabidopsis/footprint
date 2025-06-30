from __future__ import annotations

from os.path import isdir
from os.path import join
from typing import Any
from typing import TextIO
from typing import TYPE_CHECKING

import click

from .cli import config
from .utils import asuser_option
from .utils import CHECKTYPE
from .utils import config_options
from .utils import CONVERTER
from .utils import make_args
from .utils import template_option


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
    "depot_path": "where downloaded julia packages are stored [default: /home/{user}/.julia ]",
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
CELERY_SYSTEMD_HELP = f"""
Generate a systemd conf file for website background.

Use footprint config systemd-celery ... etc.
with the following params:

\b
{make_args(SUPERVISORD_ARGS)}
\b
example:
\b
footprint config systemd-celery venv=/home/ianc/miniconda3
"""


# pylint: disable=too-many-branches too-many-locals
def supervisor(  # noqa: C901
    template: str | Template,
    application_dir: str | None = None,
    args: list[str] | None = None,
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | TextIO | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
    default_values: list[tuple[str, CONVERTER]] | None = None,
) -> str:
    import os

    from .systemd import systemd
    from ..core import topath

    def isadir(key: str, s: Any) -> str | None:
        if not isdir(s):
            return f"{key}: {s} is not a directory"
        return None

    def is_julia(key: str, s: Any) -> str | None:
        if not isdir(s):
            return f"{key}: {s} is not a directory"
        if not os.access(join(s, "bin", "julia"), os.X_OK | os.R_OK):
            return f"{key}: {s} is not a *julia* directory"
        return None

    schecks: list[tuple[str, CHECKTYPE]] = [
        ("julia_dir", is_julia),
        ("depot_path", isadir),
    ]
    schecks.extend(checks or [])

    defaults: list[tuple[str, CONVERTER]] = [
        ("depot_path", lambda params: f'{params["homedir"]}/.julia'),
        ("workers", lambda _: 4),
        ("gevent", lambda _: False),
        ("stopwait", lambda _: 10),
    ]
    if default_values:
        defaults = [*default_values, *defaults]

    return systemd(
        template,
        application_dir or ".",
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
    application_dir: str | None,
    args: list[str],
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | TextIO | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
) -> None:
    from ..templating import get_templates
    from ..utils import maybe_closing, rmfiles

    templates = get_templates(template or "supervisor.ini")
    application_dir = application_dir or "."

    with maybe_closing(
        open(output, "w", encoding="utf-8") if isinstance(output, str) else output,
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
        except Exception as ex:
            if isinstance(output, str):
                rmfiles([output])
            raise ex


@config.command(name="supervisord", help=SUPERVISORD_HELP)  # noqa: C901
@config_options
@template_option
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="""location of repo or current directory""",
)
@click.argument("params", nargs=-1, required=False)
def supervisord_cmd(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
) -> None:
    supervisord(
        template,
        application_dir or ".",
        params,
        check=not no_check,
        output=output,
        ignore_unknowns=True,
    )


@config.command(name="systemd-celery", help=CELERY_SYSTEMD_HELP)  # noqa: C901
@template_option
@asuser_option
@config_options
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="""location of repo or current directory""",
)
@click.argument("params", nargs=-1, required=False)
def systemd_celery_cmd(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
    asuser: bool,
) -> None:
    import os
    from os.path import isfile

    from .utils import check_app_dir, check_venv_dir
    from .systemd import systemd

    application_dir = application_dir or "."

    def find_celery(params: dict[str, Any]) -> str | None:
        assert application_dir is not None
        for d in os.listdir(application_dir):
            fd = join(application_dir, d)
            if isdir(fd):
                for mod in ["celery", "tasks"]:
                    if isfile(join(fd, f"{mod}.py")):
                        return f"{d}.{mod}"
        return None

    def check_celery(venv: str) -> str | None:
        c = join(venv, "bin", "celery")
        if not os.access(c, os.X_OK | os.R_OK):
            return "please install celery!"
        return None

    systemd(
        template or "celery.service",
        application_dir or ".",
        params,
        help_args=SUPERVISORD_ARGS,
        check=not no_check,
        output=output,
        asuser=asuser,
        default_values=[("celery", find_celery)],
        checks=[
            ("application_dir", lambda _, v: check_app_dir(v)),
            ("venv", lambda _, v: check_venv_dir(v) or check_celery(v)),
        ],
    )
