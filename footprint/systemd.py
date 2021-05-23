import re
from os.path import abspath, dirname, isdir, isfile, join, normpath, split

import click

from .cli import cli
from .utils import rmfiles

KW = re.compile(r"^(\w+)\s*:", re.M)


def topath(path):
    return normpath(abspath(path))


def get_template(template):
    import datetime
    import sys

    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

    def ujoin(*args):
        for path in args:
            if isinstance(path, StrictUndefined):
                raise UndefinedError("application-dir' is undefined")
        return join(*args)

    templates = join(dirname(__file__), "templates")
    env = Environment(undefined=StrictUndefined, loader=FileSystemLoader([templates]))
    env.filters["normpath"] = topath
    env.globals["join"] = ujoin
    env.globals["cmd"] = " ".join(sys.argv)
    env.globals["now"] = datetime.datetime.utcnow
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

        return [(url, topath(path)) for url, path in static if path and isdir(path)]
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
            f"not a directory: {application_dir}",
            param_hint="application_dir",
        )


def check_venv_dir(venv_dir):
    import os

    if not isdir(venv_dir):
        raise click.BadParameter(
            f"not a directory: {venv_dir}",
            param_hint="params",
        )
    gunicorn = join(venv_dir, "bin", "gunicorn")
    if not os.access(gunicorn, os.X_OK | os.R_OK):
        raise click.BadParameter(
            f"{venv_dir} does not have gunicorn!", param_hint="params"
        )


def config_options(f):
    f = click.option(
        "-o", "--output", help="write to this file", type=click.Path(dir_okay=False)
    )(f)
    f = click.option("-n", "--no-check", is_flag=True, help="don't check parameters")(f)
    return f


def footprint_config(application_dir):
    import types

    f = join(application_dir, ".footprint.cfg")
    if not isfile(f):
        return {}
    with open(f, "rb") as fp:
        d = types.ModuleType("config")
        d.__file__ = f
        exec(  # pylint: disable=exec-used
            compile(fp.read(), f, mode="exec"), d.__dict__
        )
        g = {k.lower(): getattr(d, k) for k in dir(d) if k.isupper()}
    return g


@cli.group(help=click.style("nginx/systemd config commands", fg="magenta"))
def config():
    pass


SYSTEMD_HELP = """
    Generate a systemd conf file for website.

    Use footprint systemd /var/www/websites/repo ... etc.
    with the following arguments:

    \b
    application_dir : locations of repo
    appname         : application name [default: directory name]
    user            : user to run as [default: current user]
    group           : group for executable [default: current user's group]
    venv            : virtual environment to use [default: {application_dir}/../venv]
    workers         : number of gunicorn workers
                      [default: 4]
    stopwait        : seconds to wait for website to stop
    after           : start after this service [default: mysql.service]
    \b
    example:
    \b
    footprint systemd /var/www/website3/mc_msms
"""


@config.command(help=SYSTEMD_HELP)  # noqa: C901
@config_options
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("params", nargs=-1)
def systemd(application_dir, params, no_check, output):
    """Generate systemd config file.

    PARAMS are key=value arguments for the template.
    """
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    import getpass
    import grp
    import os

    from jinja2 import UndefinedError

    application_dir = topath(application_dir)

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    template = get_template("systemd.service")

    known = get_known(SYSTEMD_HELP)
    try:
        cfg = {k: v for k, v in footprint_config(application_dir).items() if k in known}
        cfg.update(fix_params(params))
        params = cfg

        for key, f in [
            ("user", getpass.getuser),
            ("group", lambda: grp.getgrgid(os.getgid()).gr_name),
            ("appname", lambda: split(application_dir)[-1]),
            ("application_dir", lambda: application_dir),
            ("venv", lambda: topath(join(application_dir, "..", "venv"))),
        ]:
            if key not in params:
                params[key] = f()

        params.setdefault("workers", 4)
        # params.setdefault("gevent", False)

        if not no_check:
            check_app_dir(application_dir)
            check_venv_dir(params["venv"])
            extra = set(params) - known
            if extra:
                raise click.BadParameter(
                    f"unknown arguments {extra}", param_hint="params"
                )

        res = template.render(**params)  # pylint: disable=no-member
        if output:
            with open(output, "w") as fp:
                fp.write(res)
        else:
            click.echo(res)
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)
        raise click.Abort()


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
    expires         : expires header for static files [default: 30d]
    listen          : listen on port [default: 80]
    \b
    example:
    \b
    footprint nginx /var/www/website3/mc_msms mcms.plantenergy.edu.au
"""


@config.command(help=NGINX_HELP)  # noqa: C901
@config_options
@click.option(
    "-r",
    "--root",
    help="root directory",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
)
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("server_name")
@click.argument("params", nargs=-1)
def nginx(application_dir, server_name, params, no_check, root, output):
    """Generate nginx config file.

    PARAMS are key=value arguments for the template.
    """
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/

    from jinja2 import UndefinedError

    application_dir = topath(application_dir)
    template = get_template("nginx.conf")

    if root:
        static = [("", topath(root))]
    else:
        static = []
    known = get_known(NGINX_HELP)
    try:
        cfg = {k: v for k, v in footprint_config(application_dir).items() if k in known}
        cfg.update(fix_params(params))
        params = cfg

        p = params.get("prefix", "")
        static.extend([(p + url, path) for url, path in get_static(application_dir)])
        params["static"] = static

        for key, f in [
            ("root", lambda: static[0][1] if static else application_dir),
            ("appname", lambda: split(application_dir)[-1]),
            ("server_name", lambda: server_name),
            ("application_dir", lambda: application_dir),
        ]:
            if key not in params:
                params[key] = f()

        if not no_check:
            check_app_dir(application_dir)

            if not isdir(params["root"]):
                raise click.BadParameter(
                    f"not a directory: {params['root']}",
                    param_hint="params",
                )
            extra = set(params) - known
            if extra:
                raise click.BadParameter(
                    f"unknown arguments {extra}", param_hint="params"
                )

        res = template.render(**params)  # pylint: disable=no-member
        if output:
            with open(output, "w") as fp:
                fp.write(res)
        else:
            click.echo(res)
    except UndefinedError as e:
        click.secho(e.message, fg="red", bold=True, err=True)
        raise click.Abort()


@config.command()
@click.option(
    "-p",
    "--port",
    default=2048,
    help="port to listen",
)
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
def nginx_server(application_dir, port):
    """Run nginx as a non daemon process."""
    import uuid

    from invoke import Context

    application_dir = topath(application_dir)
    template = get_template("nginx-test.conf")

    res = template.render(application_dir=application_dir, port=port)

    tmpfile = f"/tmp/nginx-{uuid.uuid4()}.conf"
    try:
        with open(tmpfile, "w") as fp:
            fp.write(res)
        click.secho(f"listening on http://127.0.0.1:{port}", fg="green", bold=True)
        click.secho(
            f"expecting app: cd {application_dir} && gunicorn --bind unix:app.sock app.app",
            fg="magenta",
        )
        Context().run(f"nginx -c {tmpfile}")
    finally:
        rmfiles([tmpfile])


@config.command()
@click.option(
    "-p",
    "--port",
    default=2048,
    help="port to listen",
)
@click.argument("nginxfile", type=click.File())
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
def nginx_app(nginxfile, port, application_dir):
    """Run nginx as a non daemon process using generated app config file."""
    import threading
    import uuid

    from invoke import Context

    def app():
        c = Context()
        with c.cd(application_dir):
            click.secho(
                f"starting gunicorn in {application_dir}", fg="green", bold=True
            )
            c.run("../venv/bin/gunicorn --bind unix:app.sock app.app")

    def get_server():

        A = re.compile("access_log [^;]+;")
        L = re.compile("listen [^;]+;")

        server = nginxfile.read()

        server = A.sub("", server)
        server = L.sub(f"listen {port};", server)
        server = L.sub("", server, 1)
        return server

    template = get_template("nginx-app.conf")

    res = template.render(server=get_server())

    tmpfile = f"/tmp/nginx-{uuid.uuid4()}.conf"
    try:
        with open(tmpfile, "w") as fp:
            fp.write(res)
        click.secho(f"listening on http://127.0.0.1:{port}", fg="green", bold=True)
        if application_dir:
            t = threading.Thread(target=app)
            # t.setDaemon(True)
            t.start()
        else:
            click.secho(
                "expecting app: gunicorn --bind unix:app.sock app.app",
                fg="magenta",
            )
        Context().run(f"nginx -c {tmpfile}")
    finally:
        rmfiles([tmpfile])


@config.command()
@click.option("--sudo", "use_sudo", is_flag=True, help="use sudo instead of su")
@click.argument(
    "nginxfile", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
@click.argument(
    "systemdfile",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
)
def install(nginxfile, systemdfile, use_sudo):
    """Install config files."""
    # from .utils import suresponder
    from invoke import Context
    from .utils import sudoresponder, suresponder

    c = Context()
    sudo = sudoresponder(c, lazy=True) if use_sudo else suresponder(c, lazy=True)
    conf = split(nginxfile)[-1]
    if c.run(
        f"cmp /etc/nginx/sites-enabled/{conf} {nginxfile}", hide=True, warn=True
    ).failed:
        sudo(f"cp {nginxfile} /etc/nginx/sites-enabled/")

        if sudo("nginx -t", warn=True).failed:

            sudo(f"rm /etc/nginx/sites-enabled/{conf}")
            click.secho("nginx configuration faulty", fg="red", err=True)
            raise click.Abort()

        sudo("systemctl restart nginx")
    else:
        click.secho("nginx file unchanged", fg="green")

    service = split(systemdfile)[-1]
    if c.run(
        f"cmp /etc/systemd/system/{service} {systemdfile}", hide=True, warn=True
    ).failed:
        sudo(f"cp {systemdfile} /etc/systemd/system/")
        sudo(f"systemctl enable {service}")
        sudo(f"systemctl start {service}")
        if sudo(f"systemctl status {service}", warn=True, hide=False).failed:
            sudo(f"systemctl disable {service}", warn=True)
            sudo(f"rm /etc/systemd/system/{service}")
            sudo("systemctl daemon-reload")
            click.secho("systemd configuration faulty", fg="red", err=True)
            raise click.Abort()
    else:
        click.secho("systemd file unchanged", fg="green")
    click.secho(f"{nginxfile} and {service} installed!", fg="green", bold=True)


@config.command()
@click.option("--sudo", "use_sudo", is_flag=True, help="use sudo instead of su")
@click.argument(
    "nginxfile", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
@click.argument(
    "systemdfile",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
)
def uninstall(nginxfile, systemdfile, use_sudo):
    """Uninstall config files to nginx and systemd."""

    from invoke import Context
    from .utils import sudoresponder, suresponder

    nginxfile = split(nginxfile)[-1]
    systemdfile = split(systemdfile)[-1]

    c = Context()
    sudo = sudoresponder(c, lazy=True) if use_sudo else suresponder(c, lazy=True)

    if isfile(f"/etc/nginx/sites-enabled/{nginxfile}"):
        sudo(f"rm /etc/nginx/sites-enabled/{nginxfile}")
        sudo("systemctl restart nginx")
    else:
        click.secho(f"no nginx file {nginxfile}", fg="yellow", err=True)

    if not isfile(f"/etc/systemd/system/{systemdfile}"):
        click.secho(f"no systemd service {systemdfile}", fg="yellow", err=True)
    else:
        sudo(f"systemctl stop {systemdfile}")
        sudo(f"systemctl disable {systemdfile}")
        sudo(f"rm /etc/systemd/system/{systemdfile}")
        sudo("systemctl daemon-reload")
    click.secho(f"{nginxfile} and {systemdfile} uninstalled!", fg="green", bold=True)
