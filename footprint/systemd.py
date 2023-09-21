from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from os.path import isdir
from os.path import isfile
from os.path import join
from os.path import split
from typing import Any
from typing import Callable
from typing import Dict
from typing import IO
from typing import Optional
from typing import TextIO
from typing import TYPE_CHECKING
from typing import TypeVar

import click
from jinja2 import UndefinedError

from .cli import cli
from .core import get_app_entrypoint
from .core import get_dot_env
from .core import get_static_folders_for_app
from .core import StaticFolder
from .core import topath
from .templating import get_template
from .templating import undefined_error
from .utils import get_variables
from .utils import gethomedir
from .utils import rmfiles
from .utils import which

if TYPE_CHECKING:
    from flask import Flask  # pylint: disable=unused-import
    from jinja2 import Template


F = TypeVar("F", bound=Callable[..., Any])


NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

CONVERTER = Callable[[Any], Any]


def fix_kv(
    key: str,
    values: list[str],
    convert: dict[str, CONVERTER] | None = None,
) -> tuple[str, Any]:
    # if key in {"gevent"}:  # boolean flag
    #     return ("gevent", True)
    if "" in values:
        raise UndefinedError(f"no value for {key}")
    key = key.replace("-", "_")
    if not values:  # simple key is True
        return (key, True)
    value = "=".join(values)

    def get_value(value: str) -> tuple[str, Any]:
        if key in {"user"}:  # user is a string!
            return (key, value)
        if value.isdigit():
            return (key, int(value))
        if value == "true":
            return (key, True)
        if value == "false":
            return (key, False)
        if NUM.match(value):
            return (key, float(value))
        return (key, value)

    key, v = get_value(value)
    if convert and key in convert:
        v = convert[key](v)
    return key, v


def fix_params(
    params: list[str],
    convert: dict[str, CONVERTER] | None = None,
) -> dict[str, Any]:
    def f(p: str) -> tuple[str, Any]:
        k, *values = p.split("=")
        return fix_kv(k, values, convert)

    return dict(f(p) for p in params)


# KW = re.compile(r"^([\w_-]+)\s*:", re.M)


def get_known(help_args: dict[str, str]) -> set[str]:
    return {s.replace("-", "_") for s in help_args}


def url_match(directory: str, exclude: Sequence[str] | None = None) -> str:
    # scan directory and add any extra files directories
    # that are needed for location ~ /^(match1|match2|...) { .... }

    from .config import get_config

    Config = get_config()

    if exclude is not None:
        sexclude = set(Config.exclude) | set(exclude)
    else:
        sexclude = set(Config.exclude)

    dirs = set(Config.static_dir.split("|"))
    files = set(Config.static_files.split("|"))
    for f in os.listdir(directory):
        if f in sexclude:
            continue
        tl = dirs if isdir(join(directory, f)) else files
        tl.add(f.replace(".", r"\."))

    d = "|".join(dirs)
    f = "|".join(files)
    return f"(^/({d})/|^({f})$)"


def find_favicon(application_dir: str) -> str | None:
    """Find directory with favicon.ico or robot.txt or other toplevel files"""
    from .config import get_config

    Config = get_config()

    static = {s.replace(r"\.", ".") for s in Config.static_files.split("|")}
    for d, _, files in os.walk(application_dir):
        if d.startswith((".", "_")):
            continue
        for f in files:
            if f in static:
                return d
    return None


def check_app_dir(application_dir: str) -> str | None:
    if not isdir(application_dir):
        return f"not a directory: {application_dir}"
    return None


def check_venv_dir(venv_dir: str) -> str | None:
    if not isdir(venv_dir):
        return "venv: not a directory: {venv_dir}"

    gunicorn = join(venv_dir, "bin", "gunicorn")
    if not os.access(gunicorn, os.X_OK | os.R_OK):
        return f"venv: {venv_dir} does not have gunicorn [pip install gunicorn]!"
    return None


def footprint_config(application_dir: str) -> dict[str, Any]:
    def dot_env(f: str) -> dict[str, Any]:
        cfg = get_dot_env(f)
        if cfg is None:
            return {}
        return dict(
            fix_kv(k.lower(), [v])
            for k, v in cfg.items()
            if k.isupper() and v is not None
        )

    f = join(application_dir, ".flaskenv")
    if not isfile(f):
        return {}
    return dot_env(f)


def get_default_venv(application_dir: str) -> str:
    for path in [[".venv"], ["..", "venv"]]:
        ret = topath(join(application_dir, *path))
        if isdir(ret):
            return ret
    raise RuntimeError("can't find virtual enviroment")


def has_error_page(static_folders: list[StaticFolder]) -> StaticFolder | None:
    for s in static_folders:
        if "404.html" in os.listdir(s.folder):
            return s
    return None


CHECKTYPE = Callable[[str, Any], Optional[str]]
DEFAULTTYPE = Callable[[Dict[str, Any]], Any]


def getgroup(username: str) -> str | None:
    import subprocess

    try:
        # username might not exist on this machine
        ret = subprocess.check_output(["id", "-gn", username], text=True).strip()
        return ret
    except subprocess.CalledProcessError:
        return None


def miniconda(user: str) -> str | None:
    """Find miniconda path"""
    from shutil import which as shwhich

    path = os.path.join(os.path.expanduser(f"~{user}"), "miniconda3", "bin")
    if os.path.isdir(path):
        return path
    # not really user based
    conda = shwhich("conda")
    if conda:
        return os.path.dirname(conda)
    return None


def make_args(argsd: dict[str, str], **kwargs: Any) -> str:
    from itertools import chain

    from .config import get_config

    Config = get_config()

    def color(s: str) -> str:
        if not Config.arg_color:
            return s
        return click.style(s, fg=Config.arg_color)

    args = list((k, v) for k, v in chain(argsd.items(), kwargs.items()))

    argl = [(color(k), v) for k, v in args]
    aw = len(max(argl, key=lambda t: len(t[0]))[0]) + 1
    bw = len(max(args, key=lambda t: len(t[0]))[0]) + 1
    sep = "\n  " + (" " * bw)

    def fixd(d: str) -> str:
        dl = d.split("\n")
        return sep.join(dl)

    return "\n".join(f"{arg:<{aw}}: {fixd(desc)}" for arg, desc in argl)


def run_app(
    application_dir: str,
    bind: str | None = None,
    venv: str | None = None,
    pidfile: str | None = None,
    app: str = "app.app",
) -> None:
    if pidfile is None:
        pidfile = "/tmp/gunicorn.pid"

    if venv is None:
        venv = get_default_venv(application_dir)
    msg = check_venv_dir(venv)
    if msg:
        raise click.BadParameter(msg, param_hint="params")

    bind = bind if bind else "unix:app.sock"

    cmd = [
        venv + "/bin/gunicorn",
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


def systemd_install(
    systemdfiles: list[str],  # list of systemd unit files
    asuser: bool = False,  # install as user
) -> list[str]:  # this of failed installations
    import filecmp

    from .utils import userdir

    location = userdir() if asuser else "/etc/systemd/system"

    sudo = which("sudo")
    systemctl = which("systemctl")

    def sudocmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        if not asuser:
            return subprocess.run([sudo] + list(args), check=check)
        return subprocess.run(list(args), check=check)

    def systemctlcmd(*args: str, check: bool = True) -> int:
        if not asuser:
            return subprocess.run(
                [sudo, systemctl] + list(args),
                check=check,
            ).returncode
        return subprocess.run(
            [systemctl, "--user"] + list(args),
            check=check,
        ).returncode

    failed = []
    for systemdfile in systemdfiles:
        service = split(systemdfile)[-1]
        exists = isfile(f"{location}/{service}")
        if not exists or not filecmp.cmp(f"{location}/{service}", systemdfile):
            if exists:
                click.secho(f"warning: overwriting old {service}", fg="yellow")

                ret = systemctlcmd("stop", service, check=False)

                if ret != 0:
                    click.secho(
                        "failed to stop old process [already stopped?]",
                        fg="yellow",
                        err=True,
                    )
            # will throw....
            sudocmd("cp", systemdfile, location)
            systemctlcmd("daemon-reload")
            systemctlcmd("enable", service)
            systemctlcmd("start", service)
            if systemctlcmd("status", service):
                systemctlcmd("disable", service, check=False)
                sudocmd("rm", f"{location}/{service}")
                systemctlcmd("daemon-reload")

                click.secho("systemd configuration faulty", fg="red", err=True)
                failed.append(systemdfile)

        else:
            click.secho(f"systemd file {service} unchanged", fg="green")
    return failed


def nginx_install(nginxfile: str) -> str | None:
    import filecmp
    from .config import get_config

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


def systemd_uninstall(
    systemdfiles: list[str],
    asuser: bool = False,
) -> list[str]:
    from .utils import userdir

    # install systemd file
    location = userdir() if asuser else "/etc/systemd/system"
    sudo = which("sudo")
    systemctl = which("systemctl")

    def sudocmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        if not asuser:
            return subprocess.run([sudo] + list(args), check=check)
        return subprocess.run(list(args), check=check)

    def systemctlcmd(*args: str, check: bool = True) -> int:
        if not asuser:
            return subprocess.run(
                [sudo, systemctl] + list(args),
                check=check,
            ).returncode
        return subprocess.run(
            [systemctl, "--user"] + list(args),
            check=check,
        ).returncode

    failed = []
    changed = False
    for sdfile in systemdfiles:
        systemdfile = split(sdfile)[-1]
        if "." not in systemdfile:
            systemdfile += ".service"
        filename = f"{location}/{systemdfile}"
        if not isfile(filename):
            click.secho(f"no systemd service {systemdfile}", fg="yellow", err=True)
        else:
            ret = systemctlcmd("stop", systemdfile, check=False)
            if ret != 0 and ret != 5:
                failed.append(sdfile)
            if ret == 0:
                systemctlcmd("disable", systemdfile)
                sudocmd("rm", filename)
                changed = True
    if changed:
        systemctlcmd("daemon-reload")
    return failed


def nginx_uninstall(nginxfile: str) -> None:
    from .config import get_config

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
            systemctlcmd("restart", "nginx")
            return

    click.secho(f"no nginx file {nginxfile}", fg="yellow", err=True)


SYSTEMD_ARGS = {
    "application_dir": "locations of repo",
    "appname": "application name [default: directory name]",
    "user": "user to run as [default: current user]",
    "group": "group for executable [default: current user's group]",
    "venv": "virtual environment to use [default: {application_dir}/{.venv,../venv}]",
    "workers": "number of gunicorn workers [default: (CPU*2+1)]",
    "stopwait": "seconds to wait for website to stop",
    "after": "start after this service [default: mysql.service]",
    "host": "bind gunicorn to a port [default: use unix socket]",
    "asuser": "systemd destined for --user directory",
    "miniconda": "minconda *bin* directory",
    "homedir": "$HOME (default generated from user parameter)",
}


SYSTEMD_HELP = f"""
Generate a systemd unit file for a website.

Use footprint config systemd /var/www/websites/repo ... etc.
with the following arguments:

\b
{make_args(SYSTEMD_ARGS)}
\b
example:
\b
footprint config systemd /var/www/website3/mc_msms host=8001
"""


# pylint: disable=too-many-branches too-many-locals
def systemd(  # noqa: C901
    template: str | Template,
    application_dir: str,
    args: list[str] | None = None,
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | TextIO | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    asuser: bool = False,
    ignore_unknowns: bool = False,
    default_values: list[tuple[str, DEFAULTTYPE]] | None = None,
    convert: dict[str, CONVERTER] | None = None,
) -> str:
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    import getpass
    from multiprocessing import cpu_count

    if help_args is None:
        help_args = SYSTEMD_ARGS

    application_dir = topath(application_dir)

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    template = get_template(template, application_dir)
    variables = get_variables(template)
    known = (
        get_known(help_args)
        | {"app", "asuser"}
        | (set(extra_params.keys()) if extra_params else set())
    )
    known.update(variables)
    defaults: list[tuple[str, CONVERTER]] = [
        ("application_dir", lambda _: application_dir),
        ("user", lambda _: getpass.getuser()),
        ("group", lambda params: getgroup(params["user"])),
        ("appname", lambda params: split(params["application_dir"])[-1]),
        ("venv", lambda params: get_default_venv(params["application_dir"])),
        ("miniconda", lambda params: miniconda(params["user"])),
        ("homedir", lambda params: gethomedir(params["user"])),
    ]
    if default_values:
        defaults.extend(default_values)
    defaults.extend(
        [
            ("workers", lambda _: cpu_count() * 2 + 1),
        ],
    )
    try:
        params = {
            k: v for k, v in footprint_config(application_dir).items() if k in known
        }
        params.update(fix_params(args or [], convert))
        if extra_params:
            params.update(extra_params)

        for key, default_func in defaults:
            if key not in params:
                v = default_func(params)
                if v is not None:
                    params[key] = v
                    known.add(key)

        def isint(s: str | int) -> bool:
            return isinstance(s, int) or s.isdigit()

        if "host" in params:
            h = params["host"]
            if isint(h):
                params["host"] = f"0.0.0.0:{h}"

        if check:
            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}",
                        param_hint="params",
                    )
            failed = []
            checks = list(checks or []) + [
                to_check_func("stopwait", isint, "{stopwait} is not an integer"),
                to_check_func("miniconda", isdir, "{miniconda} is not a directory"),
                to_check_func("homedir", isdir, "{homedir} is not a directory"),
            ]
            for key, func in checks:
                if key in params and key:
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

        if "asuser" not in params:
            params["asuser"] = asuser
        if "app" not in params:
            params["app"] = get_app_entrypoint(application_dir, "app.app")
        res = template.render(**params)  # pylint: disable=no-member
        to_output(res, output)
        return res
    except UndefinedError as e:
        undefined_error(e, template, params)
        raise click.Abort()


def multi_systemd(
    template: str | None,
    application_dir: str | None,
    args: list[str],
    *,
    check: bool = True,
    output: str | None = None,
    asuser: bool = False,
    ignore_unknowns: bool = False,
) -> None:
    """Generate a systemd unit file to start gunicorn for this webapp.

    PARAMS are key=value arguments for the template.
    """
    from jinja2 import Template

    from .templating import get_templates
    from .utils import maybe_closing

    def get_name(tmpl: str | Template) -> str | None:
        name = tmpl.name if isinstance(tmpl, Template) else output
        name = topath(name) if name else name

        if (
            isinstance(tmpl, Template)
            and name
            and tmpl.filename
            and name == topath(tmpl.filename)
        ):
            raise RuntimeError(f"overwriting template: {name}!")
        return name

    application_dir = application_dir or "."
    templates = get_templates(template or "systemd.service")
    for tmpl in templates:
        try:
            name = get_name(tmpl)

            with maybe_closing(
                open(name, "w", encoding="utf-8") if name else None,
            ) as fp:
                systemd(
                    tmpl,
                    application_dir,
                    args,
                    help_args=SYSTEMD_ARGS,
                    check=check,
                    output=fp,
                    asuser=asuser,
                    ignore_unknowns=ignore_unknowns,
                    checks=[
                        ("application_dir", lambda _, v: check_app_dir(v)),
                        ("venv", lambda _, v: check_venv_dir(v)),
                    ],
                    convert={"venv": topath, "application_dir": topath},
                )
        except Exception as exc:
            if isinstance(name, str):
                rmfiles([name])
            raise exc


NGINX_ARGS = {
    "server_name": "name of website",
    "application_dir": "locations of repo",
    "appname": "application name [default: directory name]",
    "root": "static files root directory",
    "root_prefix": "location prefix to use (only used if root is defined)",
    "prefix": "url prefix for application [default: /]",
    "expires": "expires header for static files [default: off] e.g. 30d",
    "listen": "listen on port [default: 80]",
    "host": "proxy to a port [default: use unix socket]",
    "root_location_match": "regex for matching static directory files",
    "access_log": "'on' or 'off'. log static asset requests [default:off]",
    "extra": "extra (legal) nginx commands for proxy",
    "ssl": "create an secure server configuration [see nginx-ssl]",
    "log_format": "specify the log_format",
}

NGINX_HELP = f"""
Generate a nginx conf file for website.

Use footprint config nginx /var/www/websites/repo website ... etc.
with the following arguments:

\b
{make_args(NGINX_ARGS)}
\b
example:
\b
footprint config nginx /var/www/website3/mc_msms mcms.plantenergy.edu.au access-log=on
"""


def to_check_func(
    key: str,
    func: Callable[[Any], bool],
    msg: str,
) -> tuple[str, CHECKTYPE]:
    def f(k: str, val: Any) -> str | None:
        if func(val):
            return None
        return msg.format(**{key: val})

    return (key, f)


def to_output(res: str, output: str | TextIO | None = None) -> None:
    if output:
        if isinstance(output, str):
            with open(output, "w", encoding="utf-8") as fp:
                fp.write(res)
                if not res.endswith("\n"):
                    fp.write("\n")
        else:
            output.write(res)
            if not res.endswith("\n"):
                output.write("\n")
    else:
        click.echo(res)


def nginx(  # noqa: C901
    application_dir: str | None,
    server_name: str,
    args: list[str] | None = None,
    *,
    app: Flask | None = None,
    template_name: str | None = None,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | TextIO | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    ignore_unknowns: bool = False,
    default_values: list[tuple[str, DEFAULTTYPE]] | None = None,
    convert: dict[str, CONVERTER] | None = None,
    ssl: bool = False,
) -> str:
    """Generate an nginx configuration for application"""

    if args is None:
        args = []
    if application_dir is None and app is not None:
        application_dir = os.path.dirname(app.root_path)
    assert application_dir is not None

    if help_args is None:
        help_args = NGINX_ARGS

    if convert is None:
        convert = {"root": topath}
    else:
        convert = {"root": topath, **convert}

    if app is None and application_dir is None:
        raise click.BadParameter("Either app or application_dir must be specified")
    assert application_dir is not None

    application_dir = topath(application_dir)
    template = get_template(template_name or "nginx.conf", application_dir)

    known = get_known(help_args) | {"staticdirs", "favicon", "error_page"}
    # directory to match with / for say /favicon.ico
    root_location_match = None
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

        staticdirs.extend(get_static_folders_for_app(application_dir, app, prefix))

        error_page = has_error_page(staticdirs)  # actually 404.html
        if error_page:
            params["error_page"] = error_page
        params["staticdirs"] = staticdirs
        for s in staticdirs:
            if not s.url:  # top level?
                root_location_match = url_match(s.folder)
        # need a root directory for server
        if "root" not in params and not staticdirs:
            raise click.BadParameter("no root directory found", param_hint="params")
        # add any defaults
        defaults: list[tuple[str, CONVERTER]] = [
            ("application_dir", lambda _: application_dir),
            ("appname", lambda params: split(params["application_dir"])[-1]),
            ("root", lambda _: staticdirs[0].folder),
            ("server_name", lambda _: server_name),
            ("ssl", lambda _: ssl),
        ] + list(default_values or [])
        for key, default_func in defaults:
            if key not in params:
                v = default_func(params)
                if v is not None:
                    params[key] = v

        if "host" in params:
            h = params["host"]
            if isinstance(h, int) or h.isdigit():
                params["host"] = f"127.0.0.1:{h}"

        if root_location_match is not None and "root_location_match" not in params:
            params["root_location_match"] = root_location_match
        if (
            "favicon" not in params
            and not root_location_match
            and application_dir is not None
        ):
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
            failed = []
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


def config_options(f: F) -> F:
    f = click.option(
        "-o",
        "--output",
        help="write to this file",
        type=click.Path(dir_okay=False),
    )(f)
    f = click.option("-n", "--no-check", is_flag=True, help="don't check parameters")(f)
    return f


# def su(f):
#     return click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")(
#         f,
#     )


def asuser_option(f: F) -> F:
    return click.option("-u", "--user", "asuser", is_flag=True, help="Install as user")(
        f,
    )


def check_user(asuser: bool) -> None:
    if asuser:
        if os.geteuid() == 0:
            raise click.BadParameter(
                "can't install to user if running as root",
                param_hint="user",
            )


def template_option(f: F) -> F:
    return click.option(
        "-t",
        "--template",
        metavar="TEMPLATE_FILE",
        help="template file or directory of templates",
    )(f)


@cli.group(help=click.style("nginx/systemd configuration commands", fg="magenta"))
def config() -> None:
    pass


@config.command(name="systemd", help=SYSTEMD_HELP)
@asuser_option
@click.option("-i", "--ignore-unknowns", is_flag=True, help="ignore unknown variables")
@template_option
@config_options
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1)
def systemd_cmd(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
    asuser: bool,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to start gunicorn for this webapp.

    PARAMS are key=value arguments for the template.
    """
    multi_systemd(
        template,
        application_dir,
        params,
        check=not no_check,
        output=output,
        ignore_unknowns=ignore_unknowns,
        asuser=asuser,
    )


TUNNEL_ARGS = {
    "local-port": "local port to connect to",
    "remote-port": "remote port to connect to",
    "keyfile": "ssh keyfile to use for target machine",
    "remote-user": "remote user to run as [default: current user]",
    "restart": "seconds to wait for before restart [default: 5]",
    "local-addr": "local address to connect [default: 127.0.0.1]",
}
TUNNEL_HELP = f"""
Generate a systemd unit file for a ssh tunnel.

Use footprint config tunnel machine ... etc.
with the following arguments:

\b
{make_args(TUNNEL_ARGS)}
\b
example:
\b
footprint config ssh-tunnel machine1 local-port=8001 remote-port=80
"""


@config.command(name="ssh-tunnel", help=TUNNEL_HELP)
@asuser_option
@click.option("-i", "--ignore-unknowns", is_flag=True, help="ignore unknown variables")
@template_option
@config_options
@click.argument(
    "target",
    required=True,
)
@click.argument("params", nargs=-1)
def tunnel_cmd(
    target: str,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
    asuser: bool,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to start ssh tunnel to TARGET.

    PARAMS are key=value arguments for the template.
    """
    systemd(
        template or "secure-tunnel.service",
        ".",
        params,
        help_args=TUNNEL_ARGS,
        check=not no_check,
        output=output,
        asuser=asuser,
        extra_params={"target": target},
        ignore_unknowns=ignore_unknowns,
        checks=[
            (
                "keyfile",
                lambda _, f: None if isfile(f) else f'keyfile "{f}" is not a file',
            ),
            (
                "restart",
                lambda _, n: None if n > 2 else "restart {n} is too short an interval",
            ),
        ],
        default_values=[
            ("local_addr", lambda _: "127.0.0.1"),
            ("restart", lambda _: 5),
            ("remote_user", lambda params: params["user"]),
        ],
        convert={"keyfile": topath},
    )


@config.command(name="template")
@asuser_option
@click.option(
    "-o",
    "--output",
    help="write to this file",
    type=click.Path(dir_okay=False),
)
@click.argument(
    "template",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
)
@click.argument("params", nargs=-1)
def template_cmd(
    params: list[str],
    template: str,
    output: str | None,
    asuser: bool,
) -> None:
    """Generate file from a jinja template.

    PARAMS are key=value arguments for the template.
    """
    systemd(
        template,
        ".",
        params,
        help_args={},
        check=False,
        output=output,
        asuser=asuser,
        ignore_unknowns=True,
    )


# pylint: disable=too-many-locals too-many-branches
@config.command(name="nginx", help=NGINX_HELP)  # noqa: C901
@template_option
@config_options
@click.option("--ssl", is_flag=True, help="make it secure")
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
)
@click.argument("server_name")
@click.argument("params", nargs=-1)
def nginx_cmd(
    application_dir: str,
    server_name: str,
    template: str | None,
    params: list[str],
    no_check: bool,
    output: str | None,
    ssl: bool = False,
) -> None:
    """Generate nginx config file.

    PARAMS are key=value arguments for the template.
    """
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    nginx(
        application_dir,
        server_name,
        params,
        template_name=template,
        check=not no_check,
        output=output,
        ssl=ssl,
    )


@config.command()
@click.option("-p", "--port", default=2048, help="port to listen", show_default=True)
@click.option(
    "-x",
    "--no-start",
    "no_start_app",
    is_flag=True,
    help="don't start the website in background",
    show_default=True,
)
@click.option("--browse", is_flag=True, help="open web application in browser")
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
def run_nginx_app(
    application_dir: str,
    port: int,
    no_start_app: bool = False,
    browse: bool = False,
) -> None:
    """Run nginx as a non daemon process with web app in background."""
    import signal
    import uuid
    from threading import Thread

    from .utils import Runner, browser

    if application_dir is None:
        application_dir = "."

    application_dir = topath(application_dir)
    tmplt = get_template("nginx-test.conf", application_dir)
    res = tmplt.render(application_dir=application_dir, port=port)

    tmpfile = f"/tmp/nginx-{uuid.uuid4()}.conf"
    pidfile = tmpfile + ".pid"

    app = get_app_entrypoint(application_dir, "app.app")

    procs: list[Runner] = []
    url = f"http://127.0.0.1:{port}"
    click.secho(f"listening on {url}", fg="green", bold=True)
    if not no_start_app:
        venv = get_default_venv(application_dir)
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
        with open(tmpfile, "w", encoding="utf-8") as fp:
            fp.write(res)
        threads = [b.start() for b in procs]
        b: Thread | None = None
        if browse:
            b = browser(url=url)
        try:
            subprocess.check_call(["nginx", "-c", tmpfile])
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
        rmfiles([tmpfile, pidfile])
        os.system("stty sane")


@config.command()
@click.option(
    "-p",
    "--port",
    default=2048,
    help="port to listen",
)
@click.option("--browse", is_flag=True, help="open web application in browser")
@click.option("--venv", help="virtual environment location")
@click.argument("nginxfile", type=click.File("rt", encoding="utf-8"))
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
def run_nginx_conf(
    nginxfile: IO[str],
    application_dir: str | None,
    port: int,
    browse: bool,
    venv: str | None,
) -> None:
    """Run nginx as a non daemon process using generated app config file."""
    import signal
    import threading
    from tempfile import NamedTemporaryFile

    from .utils import browser

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

    res = template.render(server=server)
    threads = []

    # tmpfile = f"/tmp/nginx-{uuid.uuid4()}.conf"

    with NamedTemporaryFile("w") as fp:
        fp.write(res)
        fp.flush()
        url = f"http://127.0.0.1:{port}"
        click.secho(f"listening on {url}", fg="green", bold=True)
        thrd = None
        bind = "unix:app.sock" if host is None else host
        pidfile = fp.name + ".pid"
        if application_dir:
            thrd = threading.Thread(
                target=run_app,
                args=[application_dir, bind, venv, pidfile],
            )
            # t.setDaemon(True)
            thrd.start()
            threads.append(thrd)
        else:
            click.secho(
                f"expecting app: gunicorn --bind {bind} app.app",
                fg="magenta",
            )
        if browse:
            threads.append(browser(url))
        try:
            subprocess.run(["nginx", "-c", fp.name], check=False)
        finally:
            if thrd:
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


@config.command(name="systemd-install")
@asuser_option
@click.argument(
    "systemdfiles",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
    nargs=-1,
    required=True,
)
def systemd_install_cmd(systemdfiles: list[str], asuser: bool) -> None:
    """Install systemd files."""

    check_user(asuser)

    failed = systemd_install(systemdfiles, asuser=asuser)

    if failed:
        raise click.Abort()


@config.command(name="systemd-uninstall")
@asuser_option
@click.argument(
    "systemdfiles",
    # type=click.Path(exists=True, dir_okay=False, file_okay=True),
    nargs=-1,
    required=True,
)
def systemd_uninstall_cmd(systemdfiles: list[str], asuser: bool) -> None:
    """Uninstall systemd files."""
    check_user(asuser)
    failed = systemd_uninstall(systemdfiles, asuser=asuser)
    if failed:
        click.secho(f'failed to stop: {",".join(failed)}', fg="red", err=True)
        raise click.Abort()


@config.command()
@click.option("--days", default=365, help="days of validity")
@click.argument(
    "server_name",
    required=True,
)
def nginx_ssl(server_name: str, days: int = 365) -> None:
    """Generate openssl TLS self-signed key for a website"""

    ssl_dir = "/etc/ssl"
    openssl = which("openssl")
    sudo = which("sudo")

    country = server_name.split(".")[-1].upper()

    cmd = [
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

    subprocess.run([sudo] + cmd, check=True)
    click.secho(f"written keys for {server_name} to {ssl_dir}", fg="green")
