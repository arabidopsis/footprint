import typing as t
from os.path import isdir, join, split

import click

from .systemd import (
    config,
    config_options,
    fix_params,
    footprint_config,
    get_known,
    getgroup,
)
from .templating import get_template, topath
from .utils import gethomedir

ARGS = """
    \b
    application_dir : locations of all repo
    appname         : application name [default: directory name]
    annotator       : annotator repo directory
    venv            : virtual env directory [default: {application_dir}/../venv]
    user            : user to run as [default: current user]
    group           : group to run as [default: current user group]
    workers         : number of julia and celery workers to start
                      [default: 4]
    threads         : number of julia threads to use [default: 8]
    stopwait        : seconds to wait for julia and celery to stop
                      [default: 30]
    heatbeat        : celery worker heatbeat interval in seconds
                      [default: 30]
    gevent          : run celery worker with gevent `-P gevent`
    max_interval    : interval between beats [default: 3600]
    after           : start after this service [default: mysql.service]
    celery          : celery --app to start [default: {appname}.celery]
    julia           : julia directory
    depot_path      : where downloaded julia packages are stored
                      [default: /home/{user}/.julia ]
    \b
"""
SUPERVISORD_HELP = f"""
    Generate a supervisord conf file for website background.

    Use footprint config supervisord /var/www/website/repo ... etc.
    with the following params:

    {ARGS}
    example:
    \b
    footprint config supervisord /var/www/website3/repo venv=/home/ianc/miniconda3
"""
CELERY_SYSTEMD_HELP = f"""
    Generate a systemd conf file for website background.

    Use footprint config systemd-celery /var/www/website/repo ... etc.
    with the following params:

    {ARGS}
    example:
    \b
    footprint config systemd-celery /var/www/website3/repo venv=/home/ianc/miniconda3
"""

CHECKTYPE = t.Callable[[str, t.Any], t.Optional[str]]


# pylint: disable=too-many-branches too-many-locals
def supervisor(  # noqa: C901
    template_name: str,
    application_dir: t.Optional[str] = None,
    args: t.Optional[t.List[str]] = None,
    help_str: str = ARGS,
    check: bool = True,
    output: t.Optional[t.Union[str, t.TextIO]] = None,
    extra_params: t.Optional[t.Dict[str, t.Any]] = None,
    checks: t.Optional[t.List[t.Tuple[str, CHECKTYPE]]] = None,
    ignore_unknowns: bool = False,
    asuser: bool = False,
):

    import getpass
    from itertools import chain

    from jinja2 import UndefinedError

    # if application_dir is None:
    #    application_dir = os.getcwd()

    if application_dir:
        application_dir = topath(application_dir)

    params: t.Dict[str, t.Any] = {"asuser": asuser}

    template = get_template(template_name, application_dir)
    try:
        known = get_known(help_str)
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

        DEFAULTS = [
            ("user", getpass.getuser),
            ("group", lambda: getgroup(params["user"])),
            ("depot_path", lambda: f'{gethomedir(params["user"])}/.julia'),
            ("workers", lambda: 4),
            ("gevent", lambda: False),
            ("stopwait", lambda: 10),
        ]
        if application_dir:
            DEFAULTS.extend(
                [
                    ("application_dir", lambda: application_dir),
                    ("appname", lambda: split(params["application_dir"])[-1]),
                    (
                        "venv",
                        lambda: topath(join(params["application_dir"], "..", "venv")),
                    ),
                ]
            )

        for key, f in DEFAULTS:
            if key not in params:
                v = f()
                if v is not None:
                    params[key] = v

        if check:
            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}", param_hint="params"
                    )

            def isadir(key: str, s: t.Any) -> t.Optional[str]:
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
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def supervisord(
    application_dir: t.Optional[str],
    template: t.Optional[str],
    params: t.List[str],
    no_check: bool,
    output: t.Optional[str],
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
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@click.option("-u", "--user", "asuser", is_flag=True, help="Install as user")
@config_options
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def systemd_celery(
    application_dir: t.Optional[str],
    params: t.List[str],
    template: t.Optional[str],
    no_check: bool,
    output: t.Optional[str],
    user: bool,
):
    supervisor(
        template or "celery.service",
        application_dir,
        params,
        help_str=CELERY_SYSTEMD_HELP,
        check=not no_check,
        output=output,
        asuser=user,
    )
