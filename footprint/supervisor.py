from __future__ import annotations

from os.path import isdir, join
from typing import TYPE_CHECKING, Any, TextIO

import click

from .systemd import (
    CHECKTYPE,
    DEFAULTTYPE,
    asuser_option,
    config,
    config_options,
    make_args,
    template_option,
)

if TYPE_CHECKING:
    from .templating import Template

SUPERVISORD_ARGS = {
    "application_dir": "locations of all repo",
    "appname": "application name [default: directory name]",
    "annotator": "annotator repo directory",
    "venv": "virtual env directory [default: {application_dir}/../venv]",
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

Use footprint config supervisord /var/www/website/repo ... etc.
with the following params:

\b
{make_args(SUPERVISORD_ARGS)}
\b
example:
\b
footprint config supervisord /var/www/website3/repo venv=/home/ianc/miniconda3
"""
CELERY_SYSTEMD_HELP = f"""
Generate a systemd conf file for website background.

Use footprint config systemd-celery /var/www/website/repo ... etc.
with the following params:

\b
{make_args(SUPERVISORD_ARGS)}
\b
example:
\b
footprint config systemd-celery /var/www/website3/repo venv=/home/ianc/miniconda3
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
    extra_params: dict[str, Any] = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
    default_values: list[tuple[str, DEFAULTTYPE]] | None = None,
):
    import os

    from .systemd import systemd, topath

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

    defaults = [
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
        convert=dict(julia_dir=topath, depot_path=topath),
    )


def supervisord(
    template: str | None,
    application_dir: str | None,
    args: list[str],
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | TextIO | None = None,
    extra_params: dict[str, Any] = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
) -> None:

    from .templating import get_templates

    templates = get_templates(template or "supervisor.ini")
    o: TextIO | None
    if isinstance(output, str):
        o = open(output, "wt")
    else:
        o = output

    for tplt in templates:
        supervisor(
            tplt,
            application_dir or ".",
            args,
            check=check,
            output=o,
            ignore_unknowns=ignore_unknowns,
            help_args=help_args,
            extra_params=extra_params,
            checks=checks,
            asuser=asuser,
        )
    if o is not None:
        o.close()


@config.command(name="supervisord", help=SUPERVISORD_HELP)  # noqa: C901
@config_options
@template_option
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def supervisord_cmd(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
):
    supervisord(
        template,
        application_dir,
        params,
        check=not no_check,
        output=output,
        ignore_unknowns=True,
    )


@config.command(help=CELERY_SYSTEMD_HELP)  # noqa: C901
@template_option
@asuser_option
@config_options
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def systemd_celery(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
    asuser: bool,
):
    import os
    from os.path import isfile

    from .systemd import check_app_dir, check_venv_dir, systemd

    application_dir = application_dir or "."

    def find_celery(params):
        for d in os.listdir(application_dir):
            fd = join(application_dir, d)
            if isdir(fd):
                if isfile(join(fd, "celery.py")):
                    return f"{d}.celery"
        return None

    def check_celery(venv):
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
