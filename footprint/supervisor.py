from os.path import isdir, join, split

import click

from .systemd import (
    config,
    config_options,
    fix_params,
    footprint_config,
    get_known,
    get_template,
    topath,
)

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
SYSTEMD_HELP = f"""
    Generate a systemd conf file for website background.

    Use footprint config supervisord-systemd /var/www/website/repo ... etc.
    with the following params:

    {ARGS}
    example:
    \b
    footprint config supervisord-systemd /var/www/website3/repo venv=/home/ianc/miniconda3
"""


def supervisor(
    template_name, application_dir, args=None, check=True, output: str = None
):

    import getpass
    import grp

    from jinja2 import UndefinedError

    application_dir = topath(application_dir)

    template = get_template(application_dir, template_name)
    try:
        known = get_known(ARGS)
        params = {
            k: v for k, v in footprint_config(application_dir).items() if k in known
        }

        params.update(fix_params(args or []))

        for key, f in [
            ("application_dir", lambda: application_dir),
            ("appname", lambda: split(params["application_dir"])[-1]),
            ("user", getpass.getuser),
            ("group", lambda: grp.getgrnam(params["user"]).gr_name),
            ("venv", lambda: topath(join(params["application_dir"], "..", "venv"))),
            ("depot_path", lambda: f"/home/{params['user']}/.julia"),
            ("workers", lambda: 4),
            ("gevent", lambda: False),
        ]:
            if key not in params:
                params[key] = f()

        if check:
            extra = set(params) - known
            if extra:
                raise click.BadParameter(
                    f"unknown arguments {extra}", param_hint="params"
                )
            failed = []
            for key in [
                "application_dir",
                "venv",
                "julia",
                "depot_path",
            ]:
                if key in params:
                    v = params[key]
                    if not isdir(v):
                        click.secho(
                            f"warning: not a directory: {key}={v}",
                            fg="yellow",
                            bold=True,
                            err=True,
                        )
                        failed.append(key)
                if failed:
                    raise click.Abort()

        res = template.render(**params)  # pylint: disable=no-member
        if output:
            with open(output, "w") as fp:
                fp.write(res)
        else:
            click.echo(res)
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)
        raise click.Abort()


@config.command(help=SUPERVISORD_HELP)  # noqa: C901
@config_options
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def supervisord(application_dir, params, no_check, output):
    supervisor("supervisord.ini", application_dir, params, not no_check, output)


@config.command(help=SYSTEMD_HELP)  # noqa: C901
@config_options
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1, required=False)
def supervisord_systemd(application_dir, params, no_check, output):
    supervisor("supervisord.service", application_dir, params, not no_check, output)
