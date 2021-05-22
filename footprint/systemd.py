import re
from os.path import abspath, dirname, isdir, join, normpath, split

import click

from .cli import cli

KW = re.compile(r"^(\w+)\s*:", re.M)


def get_template(template):
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

    def ujoin(*args):
        for path in args:
            if isinstance(path, StrictUndefined):
                raise UndefinedError("application-dir' is undefined")
        return join(*args)

    templates = join(dirname(__file__), "templates")
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader([templates]))
    env.filters["normpath"] = lambda f: abspath(normpath(f))
    env.globals["join"] = ujoin
    return env.get_template(template)


def fix_params(params):
    from jinja2 import UndefinedError

    def fix(key, *values):
        # if key in {"gevent"}:  # boolean flag
        #     return ("gevent", True)
        if not values or "" in values:
            raise UndefinedError(f"no value for {key}")
        return (key.replace("-", "_"), "=".join(values))

    return dict(fix(*p.split("=")) for p in params)


def get_known(help_str):
    known = {
        *KW.findall("\n".join(s.strip() for s in help_str.split("\b")[1].splitlines()))
    }
    return known


def get_static(application_dir, module="app.app"):
    import sys
    from importlib import import_module

    if application_dir not in sys.path:
        sys.path.append(application_dir)
    try:
        m = import_module(module)
        app = m.application
        static = [(app.static_url_path, app.static_folder)] + [
            (bp.static_url_path, bp.static_folder) for bp in app.blueprints.values()
        ]

        return [(url, normpath(path)) for url, path in static if path and isdir(path)]
    except (ImportError, AttributeError) as e:
        raise click.BadParameter(
            f"{application_dir} is not a website repo: {e}",
            param_hint="application_dir",
        ) from e
    finally:
        if application_dir in sys.path:
            sys.path.remove(application_dir)


def check_app_dir(application_dir):
    if not isdir(application_dir):
        raise click.BadParameter(
            f"not a directory: {application_dir}", param_hint="application_dir",
        )
    venv = join(application_dir, "..", "venv")
    if not isdir(venv):
        raise click.BadParameter(f"no virtual environment {venv}")


SYSTEMD_HELP = """
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


@cli.command(help=SYSTEMD_HELP)  # noqa: C901
@click.option("-n", "--no-check", is_flag=True, help="don't check parameters")
@click.option("-o", "--output", help="write to this file")
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("params", nargs=-1)
def systemd(application_dir, params, no_check, output):
    """Generate systemd config file.

    PARAMS are key=value arguments for the template.
    """
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    import getpass
    import grp
    import os

    from jinja2 import UndefinedError

    application_dir = abspath(normpath(application_dir))

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    template = get_template("systemd.service")

    try:
        params = fix_params(params)
        params["application_dir"] = application_dir

        for key, f in [
            ("user", getpass.getuser),
            ("group", lambda: grp.getgrgid(os.getgid()).gr_name),
            ("appname", lambda: split(application_dir)[-1]),
        ]:
            if key not in params:
                params[key] = f()

        params.setdefault("workers", 4)
        # params.setdefault("gevent", False)

        known = get_known(SYSTEMD_HELP)
        extra = set(params) - known
        if extra:
            raise click.BadParameter(f"unknown arguments {extra}", param_hint="params")

        if not no_check:
            check_app_dir(params["application_dir"])

        res = template.render(**params)  # pylint: disable=no-member
        if output:
            with open(output, "w") as fp:
                fp.write(res)
        else:
            click.echo(res)
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)


NGINX_HELP = """
    Generate a nginx conf file for website.

    Use footprint nginx /var/www/websites/repo website ... etc.
    with the following arguments:

    \b
    server_name     : name of website
    application_dir : locations of repo
    appname         : application name [default: directory name]
    root            : static files root directory
    static          : url prefix for static directory
    prefix          : url prefix for application [default: /]
    \b
    example:
    \b
    footprint nginx /var/www/website3/mc_msms mcms.plantenergy.edu.au
"""


@cli.command(help=NGINX_HELP)  # noqa: C901
@click.option("-n", "--no-check", is_flag=True, help="don't check parameters")
@click.option("-o", "--output", help="write to this file")
@click.option("-s", "--static", help="static directory")
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("website")
@click.argument("params", nargs=-1)
def nginx(application_dir, website, params, no_check, static, output):
    """Generate nginx config file.

    PARAMS are key=value arguments for the template.
    """
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    import os

    from jinja2 import UndefinedError

    application_dir = abspath(normpath(application_dir))
    template = get_template("nginx.conf")

    if static:
        static = [("", os.path.abspath(static))]
    else:
        static = []

    try:
        params = fix_params(params)
        p = params.get("prefix", "")
        static.extend([(p + url, path) for url, path in get_static(application_dir)])
        params["server_name"] = website
        params["application_dir"] = application_dir
        params["static"] = static

        for key, f in [
            ("root", lambda: static[0][1] if static else application_dir),
            ("appname", lambda: split(application_dir)[-1]),
        ]:
            if key not in params:
                params[key] = f()

        known = get_known(NGINX_HELP)
        extra = set(params) - known
        if extra:
            raise click.BadParameter(f"unknown arguments {extra}", param_hint="params")

        params["root"] = v = abspath(params["root"])
        if not no_check:
            check_app_dir(params["application_dir"])

            if not isdir(params["root"]):
                raise click.BadParameter(
                    f"not a directory: {v}", param_hint="params",
                )

        res = template.render(**params)  # pylint: disable=no-member
        if output:
            with open(output, "w") as fp:
                fp.write(res)
        else:
            click.echo(res)
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)
