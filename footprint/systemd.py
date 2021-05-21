import re
from os.path import dirname, isdir, join, normpath, split

import click

from .cli import cli

HELP = """
    Generate a systemd conf file for website.

    Use footprint systemd /var/www/websites/repo ... etc.
    with the following arguments:

    \b
    application_dir : locations of repo
    appname         : application name [default: directory name]
    user            : user to run as [default: current user]
    group           : group for executable [default: current user]
    workers         : number of gunicorn workers
                      [default: 4]
    stopwait        : seconds to wait for website to stop
                      [default: 10]

    \b
    example:
    \b
    footprint systemd /var/www/website3/mc_msms
"""

KW = re.compile(r"^(\w+)\s*:", re.M)


@cli.command(help=HELP)  # noqa: C901
@click.option("-n", "--no-check", is_flag=True, help="don't check parameters")
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("params", nargs=-1)
def systemd(application_dir, params, no_check):
    """Generate systemd config file.

    PARAMS are key=value arguments for the template.
    """
    import getpass
    import grp
    import os
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    templates = join(dirname(__file__), "templates")
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader([templates]))
    env.filters["normpath"] = normpath
    template = env.get_template("systemd.service")

    def fix(key, *values):
        # if key in {"gevent"}:  # boolean flag
        #     return ("gevent", True)
        if not values or "" in values:
            raise UndefinedError(f"no value for {key}")
        return (key.replace("-", "_"), "=".join(values))

    def ujoin(*args):
        for path in args:
            if isinstance(path, StrictUndefined):
                raise UndefinedError("application-dir' is undefined")
        return join(*args)

    try:
        params = dict(fix(*p.split("=")) for p in params)
        params["application_dir"] = application_dir
        params.setdefault("appname", split(application_dir)[-1])
        for key, f in [
            ("user", getpass.getuser),
            ("group", lambda: grp.getgrgid(os.getgid()).gr_name),
        ]:
            if key not in params:
                params[key] = f()

        params.setdefault("workers", 4)
        # params.setdefault("gevent", False)

        known = {
            *KW.findall("\n".join(s.strip() for s in HELP.split("\b")[1].splitlines()))
        }
        extra = set(params) - known
        if extra:
            raise click.BadParameter(f"unknown arguments {extra}", param_hint="args")

        if not no_check:

            v = params["application_dir"]
            if not isdir(v):
                raise click.BadParameter(
                    f"not a directory: application_dir={v}",
                    param_hint="application_dir",
                )
            venv = join(v, "..", "venv")
            if not isdir(venv):
                raise click.BadParameter("no virtual environment")

        click.echo(template.render(join=ujoin, **params))  # pylint: disable=no-member
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)
