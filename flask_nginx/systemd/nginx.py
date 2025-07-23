from __future__ import annotations

import os
import re
import subprocess
from os.path import isdir
from os.path import isfile
from os.path import join
from os.path import split
from pathlib import Path
from typing import Any
from typing import Callable
from typing import IO
from typing import TextIO
from typing import TYPE_CHECKING

import click

from ..core import get_app_entrypoint
from ..core import get_static_folders_for_app
from ..core import StaticFolder
from ..core import topath
from ..templating import get_template
from ..templating import undefined_error
from ..utils import rmfiles
from ..utils import which
from .cli import config
from .utils import asgi_option
from .utils import check_app_dir
from .utils import CHECKTYPE
from .utils import config_options
from .utils import CONVERTER
from .utils import find_favicon
from .utils import fix_params
from .utils import footprint_config
from .utils import get_default_venv
from .utils import get_known
from .utils import has_error_page
from .utils import make_args
from .utils import template_option
from .utils import to_check_func
from .utils import to_output
from .utils import url_match

if TYPE_CHECKING:
    from jinja2 import Template


def run_app(
    application_dir: str,
    gunicorn: str,
    bind: str | None = None,
    pidfile: str | None = None,
    app: str = "app.app",
) -> None:
    if pidfile is None:
        pidfile = "/tmp/gunicorn.pid"

    bind = bind if bind else "unix:app.sock"

    cmd = [
        gunicorn,
        "--pid",
        "pidfile",
        "--access-logfile=-",
        "--error-logfile=-",
        "--bind",
        bind,
        app,
    ]

    click.secho(
        f"starting gunicorn in {topath(application_dir)}",
        fg="green",
        bold=True,
    )

    click.secho(" ".join(cmd), fg="green")
    subprocess.run(cmd, cwd=application_dir, env=os.environ, check=True)


def nginx_install(nginxfile: str) -> str | None:
    import filecmp
    from ..config import get_config

    Config = get_config()

    conf = split(nginxfile)[-1]
    # Ubuntu, RHEL8
    for targetd in Config.nginx_dirs:
        if isdir(targetd):
            break
    else:
        raise RuntimeError("can't find nginx configuration directory")
    sudo = which("sudo")
    systemctl = which("systemctl")

    def sudocmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([sudo] + list(args), check=check)

    def systemctlcmd(*args: str, check: bool = True) -> int:
        return subprocess.run([sudo, systemctl] + list(args), check=check).returncode

    exists = isfile(f"{targetd}/{conf}")
    if not exists or not filecmp.cmp(f"{targetd}/{conf}", nginxfile):
        if exists:
            click.secho(f"warning: overwriting old {conf}", fg="yellow")

        sudocmd("cp", nginxfile, f"{targetd}/")

        if sudocmd("nginx", "-t", check=False).returncode != 0:
            sudocmd("rm", f"{targetd}/{conf}", check=True)
            click.secho("nginx configuration faulty", fg="red", err=True)
            return None

        systemctlcmd("restart", "nginx")
    else:
        click.secho(f"nginx file {conf} unchanged", fg="green")
    return conf


def nginx_uninstall(nginxfile: str) -> None:
    from ..config import get_config

    Config = get_config()

    nginxfile = split(nginxfile)[-1]
    if "." not in nginxfile:
        nginxfile += ".conf"
    sudo = which("sudo")
    systemctl = which("systemctl")

    def sudocmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([sudo] + list(args), check=check)

    def systemctlcmd(*args: str, check: bool = True) -> int:
        return subprocess.run([sudo, systemctl] + list(args), check=check).returncode

    for d in Config.nginx_dirs:
        fname = join(d, nginxfile)
        if isfile(fname):
            sudocmd("rm", fname)
            systemctlcmd("restart", "nginx.service")
            return

    click.secho(f"no nginx file {nginxfile}", fg="yellow", err=True)


NGINX_ARGS = {
    "server_name": "name of website",
    "application_dir": "locations of repo",
    "app": "entrypoint to app",
    "appname": "application name [default: directory name]",
    "root": "static files root directory",
    "root_prefix": "location prefix to use (only used if root is defined)",
    "prefix": "url prefix for application [default: /]",
    "expires": "expires header for static files [default: off] e.g. 30d",
    "listen": "listen on port [default: 80]",
    "host": "proxy to a port [default: use unix socket]",
    "port": "TCP/IP port to use",
    "root_location_match": "regex for matching static directory files",
    "access_log": "'on' or 'off'. log static asset requests [default:off]",
    "extra": "extra (legal) nginx commands for proxy",
    "ssl": "create an secure server configuration [see nginx-ssl]",
    "log_format": "specify the log_format",
    "authentication": "authentication file",
}

NGINX_HELP = f"""
Generate a nginx conf file for website.

Use footprint config nginx website ... etc.
with the following arguments:

\b
{make_args(NGINX_ARGS)}
\b
example:
\b
footprint config nginx mcms.plantenergy.edu.au access-log=on
"""


def appname_func(params: dict[str, Any]) -> str:
    app = str(params["application_dir"])
    return str(split(app)[-1])


def nginx(  # noqa: C901
    application_dir: str,
    server_name: str,
    args: list[str] | None = None,
    *,
    template_name: str | None = None,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | TextIO | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    default_values: list[tuple[str, CONVERTER]] | None = None,
    convert: dict[str, Callable[[Any], Any]] | None = None,
    ssl: bool = False,
    asgi: bool = False,
) -> str:
    """Generate an nginx configuration for application"""
    from jinja2 import UndefinedError

    if args is None:
        args = []

    if application_dir is None:
        raise click.BadParameter("no application directory")

    if help_args is None:
        help_args = NGINX_ARGS

    if convert is None:
        convert = {"root": topath}
    else:
        convert = {"root": topath, **convert}

    application_dir = topath(application_dir)
    template = get_template(template_name or "nginx.conf", application_dir)

    known = get_known(help_args) | {"staticdirs", "favicon", "error_page"}
    # directory to match with / for say /favicon.ico
    root_location_match = None
    params: dict[str, Any] = {}
    try:
        # arguments from .flaskenv
        params = {
            k: v for k, v in footprint_config(application_dir).items() if k in known
        }
        params.update(fix_params(args, convert))
        if extra_params:
            params.update(extra_params)

        prefix = params.get("prefix", "")
        if "root" in params:
            root = topath(join(application_dir, str(params["root"])))
            params["root"] = root
            rp = params.get("root_prefix", None)
            staticdirs = [StaticFolder(rp if rp is not None else prefix, root, False)]
        else:
            staticdirs = []
        # if the params have an app value use that as the entrypoint
        entrypoint = params.get("app", None)
        if entrypoint is None:
            entrypoint = get_app_entrypoint(application_dir, asgi=asgi)
        if entrypoint != "@none":
            staticdirs.extend(
                get_static_folders_for_app(
                    application_dir,
                    entrypoint=entrypoint,
                    prefix=prefix,
                ),
            )

        error_page = has_error_page(staticdirs)  # actually 404.html
        if error_page:
            params["error_page"] = error_page
        params["staticdirs"] = staticdirs
        for s in staticdirs:
            if not s.url:  # top level?
                root_location_match = url_match(s.folder)
        # need a root directory for server
        if "root" not in params and not staticdirs:
            params["root"] = topath(application_dir)
            # raise click.BadParameter("no root directory found", param_hint="params")
        # add any defaults
        defaults: list[tuple[str, CONVERTER]] = [
            ("application_dir", lambda _: application_dir),
            ("appname", appname_func),
            ("root", lambda _: staticdirs[0].folder),
            ("server_name", lambda _: server_name),
            ("ssl", lambda _: ssl),
        ]

        if default_values:
            defaults.extend(default_values)

        for key, default_func in defaults:
            if key not in params:
                v = default_func(params)
                if v is not None:
                    params[key] = v

        def isint(s: str | int) -> bool:
            return isinstance(s, int) or s.isdigit()

        if "host" in params:
            h = params["host"]
            if isint(h):
                params["host"] = "127.0.0.1"
                params["port"] = h
            else:
                if ":" in h:
                    s, h = h.rsplit(":", maxsplit=1)
                    params["host"] = s
                    params["port"] = h

        if "port" not in params:
            params["port"] = 8000

        if root_location_match is not None and "root_location_match" not in params:
            params["root_location_match"] = root_location_match
        if "favicon" not in params and not root_location_match:
            d = find_favicon(application_dir)
            if d:
                params["favicon"] = topath(join(application_dir, d))

        if "favicon" in params:
            params["favicon"] = topath(params["favicon"])

        if check:
            msg = check_app_dir(application_dir)
            if msg:
                raise click.BadParameter(msg, param_hint="application_dir")

            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}",
                        param_hint="params",
                    )
            failed: list[str] = []
            checks = (checks or []) + [
                to_check_func("root", isdir, '"{root}" is not a directory'),
                to_check_func("favicon", isdir, '"{favicon}" is not a directory'),
            ]
            for key, func in checks:
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
        to_output(res, output)
        return res
    except UndefinedError as e:
        undefined_error(e, template, params)
        raise click.Abort()


# pylint: disable=too-many-locals too-many-branches
@config.command(name="nginx", help=NGINX_HELP)  # noqa: C901
@template_option
@config_options
@click.option("--ssl", is_flag=True, help="make it secure")
@click.option(
    "--no-static",
    is_flag=True,
    help="Don't try to introspect static files. (Useful for non-flask websites)",
)
@asgi_option
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="""location of repo or current directory""",
)
@click.argument("server_name")
@click.argument("params", nargs=-1)
def nginx_cmd(
    application_dir: str | None,
    server_name: str,
    template: str | None,
    asgi: bool,
    params: list[str],
    no_check: bool,
    output: str | None,
    ssl: bool = False,
    no_static: bool = False,
) -> None:
    """Generate nginx config file.

    PARAMS are key=value arguments for the template.
    """
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/

    if no_static:
        params = [*params, "app=@none"]
    nginx(
        application_dir or ".",
        server_name,
        params,
        template_name=template,
        check=not no_check,
        output=output,
        ssl=ssl,
        asgi=asgi,
    )


@config.command(name="nginx-run-app")
@click.option("-p", "--port", default=5000, help="port to listen", show_default=True)
@click.option(
    "-x",
    "--no-start",
    "no_start_app",
    is_flag=True,
    help="don't start the website in background",
    show_default=True,
)
@click.option(
    "--entrypoint",
    help="web application entrypoint",
)
@asgi_option
@click.option("--browse", is_flag=True, help="open web application in browser")
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="""location of repo or current directory""",
)
def nginx_run_app_cmd(
    application_dir: str | None,
    port: int,
    entrypoint: str | None,
    asgi: bool,
    no_start_app: bool = False,
    browse: bool = False,
) -> None:
    """Run nginx as a non daemon process with web app in background."""
    import signal
    import uuid
    from threading import Thread

    from tempfile import gettempdir

    from ..utils import Runner, browser

    nginx_exe = which("nginx")

    if application_dir is None:
        application_dir = "."

    application_dir = topath(application_dir)
    tmplt = get_template("nginx-test.conf", application_dir)
    res = tmplt.render(  # pylint: disable=no-member
        application_dir=application_dir,
        port=port,
    )
    tmpfile = Path(gettempdir()) / f"nginx-{uuid.uuid4()}.conf"
    pidfile = str(tmpfile) + ".pid"

    app = entrypoint or get_app_entrypoint(application_dir, asgi=asgi)

    procs: list[Runner] = []
    url = f"http://127.0.0.1:{port}"
    click.secho(f"listening on {url}", fg="green", bold=True)
    if not no_start_app:
        venv = get_default_venv()
        if os.path.isdir(venv):
            gunicorn = os.path.join(venv, "bin", "gunicorn")
        else:
            gunicorn = which("gunicorn")

        bgapp = Runner(
            app,
            [gunicorn, "--pid", pidfile, "--bind", "unix:app.sock", app],
            directory=application_dir,
        )
        procs.append(bgapp)
    else:
        click.secho(
            f"expecting app: cd {application_dir} && gunicorn --bind unix:app.sock {app}",
            fg="magenta",
            bold=True,
        )
    try:
        with tmpfile.open("wt", encoding="utf-8") as fp:
            fp.write(res)
        threads = [b.start() for b in procs]
        b: Thread | None = None
        if browse:
            b = browser(url=url)
        try:
            subprocess.check_call([nginx_exe, "-c", str(tmpfile)])
        finally:
            if not no_start_app:
                with open(pidfile, encoding="utf-8") as fp:
                    pid = int(fp.read().strip())
                    os.kill(pid, signal.SIGINT)

            for thrd in threads:
                thrd.wait()
            if b:
                b.join()
    finally:
        tmpfile.unlink(missing_ok=True)
        rmfiles([pidfile])
        os.system("stty sane")


@config.command(name="nginx-run")
@click.option(
    "-p",
    "--port",
    default=5000,
    help="port for nginx to listen",
)
@click.option(
    "--entrypoint",
    help="web application entrypoint",
)
@asgi_option
@click.option("--browse", is_flag=True, help="open web application in browser")
@click.option("--venv", help="virtual environment location")
@click.argument("nginxfile", type=click.File("rt", encoding="utf-8"))
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="""location of repo or current directory""",
)
def nginx_run_cmd(
    nginxfile: IO[str],
    application_dir: str | None,
    entrypoint: str | None,
    port: int,
    browse: bool,
    venv: str | None,
    asgi: bool,
) -> None:
    """Run nginx as a non daemon process using generated app config file.

    This will test the generated nginx configuration file
    """
    import signal
    import threading
    from tempfile import NamedTemporaryFile

    from ..utils import browser

    nginx_exe = which("nginx")

    def once(m: str) -> Callable[[re.Match[str]], str]:
        done = False

        def f(r: re.Match[str]) -> str:
            nonlocal done
            if done:
                return ""
            done = True
            return m

        return f

    def get_server() -> tuple[str, str | None]:
        """parse nginx.conf file for server and host"""

        def tohost(h: str) -> str | None:
            if h.startswith("unix:"):
                return None
            return h

        A = re.compile("access_log [^;]+;")
        L = re.compile("listen [^;]+;")
        H = re.compile(r"proxy_pass\s+http://([^/\s]+)/?\s*;")
        S = re.compile(r"server\s+([^{\s]+)/?.*;")

        server = nginxfile.read()
        # remove old access_log and replace listen commands
        server = A.sub("", server)
        server = L.sub(once(f"listen {port};"), server)

        m = S.search(server) or H.search(server)

        return server, None if not m else tohost(m.group(1))

    template: Template = get_template("nginx-app.conf", application_dir)
    server, host = get_server()
    application_dir = application_dir or "."

    res = template.render(server=server)  # pylint: disable=no-member
    threads: list[threading.Thread] = []

    with NamedTemporaryFile("w") as fp:
        fp.write(res)
        fp.flush()
        url = f"http://127.0.0.1:{port}"
        click.secho(f"listening on {url}", fg="green", bold=True)
        thrd = None
        bind = "unix:app.sock" if host is None else host
        pidfile = fp.name + ".pid"
        if application_dir:
            if venv is None:
                venv = get_default_venv()

            gunicorn = Path(venv) / "bin" / "gnuicorn"
            if not gunicorn.exists():
                gunicorn = Path(which("gunicorn"))
            entry = entrypoint or get_app_entrypoint(application_dir, asgi=asgi)
            thrd = threading.Thread(
                target=run_app,
                args=[application_dir, str(gunicorn), bind, pidfile, entry],
            )
            # t.setDaemon(True)
            thrd.start()
            threads.append(thrd)
        else:
            entry = entrypoint or get_app_entrypoint(application_dir or ".", asgi=asgi)
            click.secho(
                f"expecting app: gunicorn --bind {bind} {entry}",
                fg="magenta",
            )
        if browse:
            threads.append(browser(url))
        try:
            subprocess.run([nginx_exe, "-c", fp.name], check=False)
        finally:
            if thrd:
                if os.path.isfile(pidfile):
                    with open(pidfile, encoding="utf-8") as fp2:
                        pid = int(fp2.read().strip())
                        os.kill(pid, signal.SIGINT)
            for thrd in threads:
                thrd.join(timeout=2.0)
            rmfiles([pidfile])
            os.system("stty sane")


@config.command(name="nginx-install")
@click.argument(
    "nginxfile",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
)
def nginx_install_cmd(nginxfile: str) -> None:
    """Install nginx config file."""

    # install frontend
    conf = nginx_install(nginxfile)
    if conf is None:
        raise click.Abort()

    click.secho(f"{conf} installed!", fg="green", bold=True)


@config.command(name="nginx-uninstall")
@click.argument("nginxfile")
def nginx_uninstall_cmd(nginxfile: str) -> None:
    """Uninstall nginx config file."""

    nginx_uninstall(nginxfile)

    click.secho(f"{nginxfile} uninstalled!", fg="green", bold=True)


@config.command(name="nginx-ssl")
@click.option("--days", default=365, help="days of validity")
@click.argument(
    "server_name",
    required=True,
)
def nginx_ssl_cmd(server_name: str, days: int = 365) -> None:
    """Generate openssl TLS self-signed key for a website"""

    ssl_dir = "/etc/ssl"
    openssl = which("openssl")
    sudo = which("sudo")

    country = server_name.split(".")[-1].upper()

    cmd = [
        sudo,
        openssl,
        "req",
        "-x509",
        "-nodes",
        "-days",
        str(days),
        "-newkey",
        "rsa:2048",
        "-keyout" f"{ssl_dir}/private/{server_name}.key" "-out",
        f"{ssl_dir}/certs/{server_name}.crt" "-subj",
        f"/C={country}/CN={server_name}",
    ]

    subprocess.run(cmd, check=True)
    click.secho(f"written keys for {server_name} to {ssl_dir}", fg="green")
