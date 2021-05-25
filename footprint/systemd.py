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
    return {
        *KW.findall("\n".join(s.strip() for s in help_str.split("\b")[1].splitlines()))
    }


def url_match(directory):
    import os
    from .config import STATIC_DIR, STATIC_FILES

    dirs = set(STATIC_DIR.split("|"))
    files = set(STATIC_FILES.split("|"))
    for f in os.listdir(directory):
        t = dirs if isdir(join(directory, f)) else files
        t.add(f.replace(".", r"\."))

    d = "|".join(dirs)
    f = "|".join(files)
    return f"(^/({d})/|^({f})$)"


def get_static_folders(application_dir, module="app.app"):
    import sys
    from importlib import import_module

    STATIC_RULE = re.compile("^(.*)/<path:filename>$")

    def get_static_folder(rule):
        bound_method = app.view_functions[rule.endpoint]
        # self.send_static_file
        # __self__ is the blueprint
        if hasattr(bound_method, "static_folder"):
            return bound_method.static_folder
        if not hasattr(bound_method, "__self__"):
            return None
        return bound_method.__self__.static_folder

    def find_static(app):
        for r in app.url_map.iter_rules():
            if not r.endpoint.endswith("static"):
                continue
            m = STATIC_RULE.match(r.rule)
            if not m:
                continue
            prefix = m.group(1)
            folder = get_static_folder(r)
            if folder is None:
                click.secho(
                    f"location: can't find static folder for endpoint: {r.endpoint}",
                    fg="red",
                    err=True,
                )
                continue
            if not folder.endswith(prefix):
                click.secho(
                    f"location: incomensurate prefix {prefix} for folder {folder}",
                    fg="red",
                    err=True,
                )
                continue
            if len(prefix) > 0:
                folder = folder[: -len(prefix)]
            if not isdir(folder):
                continue
            yield prefix, topath(folder)

    remove = False
    if application_dir not in sys.path:
        sys.path.append(application_dir)
        remove = True
    try:
        m = import_module(module)
        app = m.application
        return list(find_static(app))

    except (ImportError, AttributeError) as e:
        raise click.BadParameter(
            f"can't load application from {application_dir}: {e}"
        ) from e
    finally:
        if remove:
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
            f"venv: not a directory: {venv_dir}",
            param_hint="params",
        )
    gunicorn = join(venv_dir, "bin", "gunicorn")
    if not os.access(gunicorn, os.X_OK | os.R_OK):
        raise click.BadParameter(
            f"venv: {venv_dir} does not have gunicorn!", param_hint="params"
        )


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


def run_app(application_dir, host=None, venv=None):
    from invoke import Context

    c = Context()
    if venv is None:
        venv = topath(join(application_dir, "..", "venv"))
    check_venv_dir(venv)
    with c.cd(application_dir):
        click.secho(f"starting gunicorn in {application_dir}", fg="green", bold=True)
        bind = "unix:app.sock" if host is None else host
        c.run(f"{venv}/bin/gunicorn --bind {bind} app.app")


def config_options(f):
    f = click.option(
        "-o", "--output", help="write to this file", type=click.Path(dir_okay=False)
    )(f)
    f = click.option("-n", "--no-check", is_flag=True, help="don't check parameters")(f)
    return f


@cli.group(help=click.style("nginx/systemd config commands", fg="magenta"))
def config():
    pass


SYSTEMD_HELP = """
    Generate a systemd conf file for website.

    Use footprint config systemd /var/www/websites/repo ... etc.
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
    host            : bind gunicorn to a port [default: use unix socket]
    \b
    example:
    \b
    footprint config systemd /var/www/website3/mc_msms
"""


@config.command(help=SYSTEMD_HELP)  # noqa: C901
@config_options
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("params", nargs=-1)
def systemd(application_dir, params, no_check, output):
    """Generate systemd config file to start gunicorn.

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
            ("workers", lambda: 4),
        ]:
            if key not in params:
                params[key] = f()

        if "host" in params:
            h = params["host"]
            if h.isdigit():
                params["host"] = f"0.0.0.0:{h}"
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

    Use footprint config nginx /var/www/websites/repo website ... etc.
    with the following arguments:

    \b
    server_name     : name of website
    application_dir : locations of repo
    appname         : application name [default: directory name]
    root            : static files root directory
    prefix          : url prefix for application [default: /]
    expires         : expires header for static files [default: 30d]
    listen          : listen on port [default: 80]
    host            : proxy to a port [default: use unix socket]
    match           : regex for matching static directory files
    \b
    example:
    \b
    footprint config nginx /var/www/website3/mc_msms mcms.plantenergy.edu.au
"""


@config.command(help=NGINX_HELP)  # noqa: C901
@config_options
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("server_name")
@click.argument("params", nargs=-1)
def nginx(application_dir, server_name, params, no_check, output):
    """Generate nginx config file.

    PARAMS are key=value arguments for the template.
    """
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/

    from jinja2 import UndefinedError

    application_dir = topath(application_dir)
    template = get_template("nginx.conf")

    known = get_known(NGINX_HELP) | {"static"}
    match = None
    try:
        cfg = {k: v for k, v in footprint_config(application_dir).items() if k in known}
        cfg.update(fix_params(params))
        params = cfg

        p = params.get("prefix", "")

        if "root" in params:
            params["root"] = root = topath(join(application_dir, params["root"]))
            static = [("", root)]
        else:
            static = []
        static.extend(
            [(p + url, path) for url, path in get_static_folders(application_dir)]
        )
        params["static"] = static
        for url, path in static:
            if not url:
                match = url_match(path)
        # need a root directory for server
        if "root" not in params and not static:
            raise click.BadParameter("no root directory found", param_hint="root")

        for key, f in [
            ("root", lambda: static[0][1]),
            ("appname", lambda: split(application_dir)[-1]),
            ("server_name", lambda: server_name),
            ("application_dir", lambda: application_dir),
        ]:
            if key not in params:
                params[key] = f()

        if "host" in params:
            h = params["host"]
            if h.isdigit():
                params["host"] = f"127.0.0.1:{h}"
        if match is not None and "match" not in params:
            params["match"] = match

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

    def get_server():
        def tohost(h):
            if h.startswith("unix:"):
                return None
            return h

        A = re.compile("access_log [^;]+;")
        L = re.compile("listen [^;]+;")
        H = re.compile(r"proxy_pass\s+http://([^/\s]+)/?\s*;")

        server = nginxfile.read()
        # remove old access_log and listen commands
        server = A.sub("", server)
        server = L.sub(f"listen {port};", server)
        server = L.sub("", server, 1)
        m = H.search(server)
        return server, None if not m else tohost(m.group(1))

    template = get_template("nginx-app.conf")
    server, host = get_server()

    res = template.render(server=server)

    tmpfile = f"/tmp/nginx-{uuid.uuid4()}.conf"
    try:
        with open(tmpfile, "w") as fp:
            fp.write(res)
        click.secho(f"listening on http://127.0.0.1:{port}", fg="green", bold=True)
        if application_dir:
            t = threading.Thread(target=run_app, args=[application_dir, host])
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
    exists = isfile(f"/etc/nginx/sites-enabled/{conf}")
    if (
        not exists
        or c.run(
            f"cmp /etc/nginx/sites-enabled/{conf} {nginxfile}", hide=True, warn=True
        ).failed
    ):
        if exists:
            click.secho(f"warning: overwriting old {conf}", fg="yellow")
        sudo(f"cp {nginxfile} /etc/nginx/sites-enabled/")

        if sudo("nginx -t", warn=True).failed:

            sudo(f"rm /etc/nginx/sites-enabled/{conf}")
            click.secho("nginx configuration faulty", fg="red", err=True)
            raise click.Abort()

        sudo("systemctl restart nginx")
    else:
        click.secho("nginx file unchanged", fg="green")

    service = split(systemdfile)[-1]
    exists = isfile(f"/etc/systemd/system/{service}")
    if (
        not exists
        or c.run(
            f"cmp /etc/systemd/system/{service} {systemdfile}", hide=True, warn=True
        ).failed
    ):
        if exists:
            click.secho("warning: overwriting old {service}", fg="yellow")
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
