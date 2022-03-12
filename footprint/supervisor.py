from __future__ import annotations

from os.path import isdir, join, split
from typing import Any, Callable, Optional, TextIO

import click

from .systemd import (
    asuser_option,
    config,
    config_options,
    fix_params,
    footprint_config,
    get_known,
    getgroup,
    make_args,
    template_option,
)
from .templating import get_template, topath
from .utils import gethomedir

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
    example:
    \b
    footprint config systemd-celery /var/www/website3/repo venv=/home/ianc/miniconda3
"""

CHECKTYPE = Callable[[str, Any], Optional[str]]


# pylint: disable=too-many-branches too-many-locals
def supervisor(  # noqa: C901
    template_name: str,
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
):

    import getpass
    from itertools import chain

    from jinja2 import UndefinedError

    # if application_dir is None:
    #    application_dir = os.getcwd()
    if help_args is None:
        help_args = SUPERVISORD_ARGS
    if application_dir:
        application_dir = topath(application_dir)

    params: dict[str, Any] = {"asuser": asuser}

    template = get_template(template_name, application_dir)
    try:
        known = get_known(help_args) | {"asuser"}
        if application_dir:
            params.update(
                {
                    k: v
                    for k, v in footprint_config(application_dir).items()
                    if k in known
                }
            )

        params.update(fix_params(args or []))
        if extra_params:
            params.update(extra_params)

        defaults = [
            ("user", lambda _: getpass.getuser()),
            ("group", lambda params: getgroup(params["user"])),
            ("depot_path", lambda params: f'{gethomedir(params["user"])}/.julia'),
            ("workers", lambda _: 4),
            ("gevent", lambda _: False),
            ("stopwait", lambda _: 10),
        ]
        if application_dir:
            defaults.extend(
                [
                    ("application_dir", lambda _: application_dir),
                    ("appname", lambda params: split(params["application_dir"])[-1]),
                    (
                        "venv",
                        lambda params: topath(
                            join(params["application_dir"], "..", "venv")
                        ),
                    ),
                ]
            )

        for key, default_func in defaults:
            if key not in params:
                v = default_func(params)
                if v is not None:
                    params[key] = v

        if check:
            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}", param_hint="params"
                    )

            def isadir(key: str, s: Any) -> str | None:
                if not isdir(s):
                    return f"{key}: {s} is not a directory"
                return None

            CHECKS = [
                ("venv", isadir),
                ("julia", isadir),
                ("depot_path", isadir),
            ]
            if application_dir:
                CHECKS.append(("application_dir", isadir))

            failed = []
            for key, func in chain(checks or [], CHECKS):
                if key in params:
                    v = params[key]
                    msg = func(key, v)
                    if msg is not None:
                        click.secho(
                            msg,
                            fg="yellow",
                            bold=True,
                            err=True,
                        )
                        failed.append(key)
                if failed:
                    raise click.Abort()

        res = template.render(**params)  # pylint: disable=no-member
        if output:
            if isinstance(output, str):
                with open(output, "w") as fp:
                    fp.write(res)
            else:
                output.write(res)
        else:
            click.echo(res)
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)
        raise click.Abort()


@config.command(help=SUPERVISORD_HELP)  # noqa: C901
@config_options
@template_option
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def supervisord(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
):
    import os

    supervisor(
        template or "supervisord.ini",
        application_dir or os.getcwd(),
        params,
        check=not no_check,
        output=output,
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

    def check_celery(key, venv):
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
            ("venv", lambda _, v: check_venv_dir(v)),
            ("venv", check_celery),
        ],
    )
