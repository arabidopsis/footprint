from __future__ import annotations

import os
import re
from os.path import isdir, isfile, join, split
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, TextIO, TypeVar

import click

from .cli import cli
from .core import (
    StaticFolder,
    get_app_entrypoint,
    get_dot_env,
    get_static_folders_for_app,
)
from .templating import get_template, topath
from .utils import SUDO, get_sudo, gethomedir, rmfiles

if TYPE_CHECKING:
    from flask import Flask  # pylint: disable=unused-import
    from invoke import Context  # pylint: disable=unused-import
    from jinja2 import Template


F = TypeVar("F", bound=Callable[..., Any])


NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

CONVERTER = Callable[[Any], Any]


def fix_kv(
    key: str, values: list[str], convert: dict[str, CONVERTER] | None = None
) -> tuple[str, Any]:
    from jinja2 import UndefinedError

    # if key in {"gevent"}:  # boolean flag
    #     return ("gevent", True)
    if "" in values:
        raise UndefinedError(f"no value for {key}")
    key = key.replace("-", "_")
    if not values:  # simple key is True
        return (key, True)
    value = "=".join(values)

    def get_value(value):
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

    key, value = get_value(value)
    if convert and key in convert:
        value = convert[key](value)
    return key, value


def fix_params(
    params: list[str], convert: dict[str, CONVERTER] | None = None
) -> dict[str, Any]:
    def f(p):
        k, *values = p.split("=")
        return fix_kv(k, values, convert)

    return dict(f(p) for p in params)


# KW = re.compile(r"^([\w_-]+)\s*:", re.M)


def get_known(help_args: dict[str, str]) -> set[str]:
    return {s.replace("-", "_") for s in help_args}


def url_match(directory: str, exclude=None) -> str:
    # scan directory and add any extra files directories
    # that are needed for location ~ /^(match1|match2|...) { .... }

    from .config import EXCLUDE, STATIC_DIR, STATIC_FILES

    if exclude is not None:
        exclude = set(EXCLUDE) | set(exclude)
    else:
        exclude = set(EXCLUDE)

    dirs = set(STATIC_DIR.split("|"))
    files = set(STATIC_FILES.split("|"))
    for f in os.listdir(directory):
        if f in exclude:
            continue
        tl = dirs if isdir(join(directory, f)) else files
        tl.add(f.replace(".", r"\."))

    d = "|".join(dirs)
    f = "|".join(files)
    return f"(^/({d})/|^({f})$)"


def find_favicon(application_dir: str) -> str | None:
    """Find directory with favicon.ico or robot.txt or other toplevel files"""
    from .config import STATIC_FILES

    static = {s.replace(r"\.", ".") for s in STATIC_FILES.split("|")}
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
    # import types

    def dot_env(f: str):
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
    return topath(join(application_dir, "..", "venv"))


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
        return subprocess.check_output(["id", "-gn", username], text=True).strip()
    except subprocess.CalledProcessError:
        return None


def miniconda(user):
    """Find miniconda path"""
    from invoke import Context  # pylint: disable=redefined-outer-name

    path = os.path.join(os.path.expanduser(f"~{user}"), "miniconda3", "bin")
    if os.path.isdir(path):
        return path
    # not really user based
    conda = Context().run("which conda", warn=True, hide=True).stdout.strip()
    if conda:
        return os.path.dirname(conda)
    return None


def make_args(argsd: dict[str, str], **kwargs) -> str:
    from itertools import chain

    from .config import ARG_COLOR

    def color(s):
        if not ARG_COLOR:
            return s
        return click.style(s, fg=ARG_COLOR)

    args = list((k, v) for k, v in chain(argsd.items(), kwargs.items()))

    argl = [(color(k), v) for k, v in args]
    aw = len(max(argl, key=lambda t: len(t[0]))[0]) + 1
    bw = len(max(args, key=lambda t: len(t[0]))[0]) + 1
    sep = "\n  " + (" " * bw)

    def fixd(d):
        d = d.split("\n")
        return sep.join(d)

    return "\n".join(f"{arg:<{aw}}: {fixd(desc)}" for arg, desc in argl)


def run_app(
    application_dir: str,
    bind: str | None = None,
    venv: str | None = None,
    pidfile: str | None = None,
    app: str = "app.app",
) -> None:
    from invoke import Context  # pylint: disable=redefined-outer-name

    if pidfile is None:
        pidfile = "/tmp/gunicorn.pid"

    if venv is None:
        venv = get_default_venv(application_dir)
    msg = check_venv_dir(venv)
    if msg:
        raise click.BadParameter(msg, param_hint="params")
    c = Context()
    with c.cd(application_dir):
        bind = bind if bind else "unix:app.sock"
        cmd = f"{venv}/bin/gunicorn  --pid {pidfile} --access-logfile=- --error-logfile=- --bind {bind} {app}"
        click.secho(
            f"starting gunicorn in {topath(application_dir)}", fg="green", bold=True
        )
        click.secho(cmd, fg="green")
        c.run(cmd, pty=True)


def systemd_install(
    systemdfiles: list[str],  # list of systemd unit files
    context: Context | None = None,  # invoke context
    sudo: SUDO | None = None,  # use this sudo runner
    asuser: bool = False,  # install as user
    use_su: bool = False,  # use su instead of sudo to install
) -> list[str]:  # this of failed installations

    # install systemd file
    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import userdir

    if context is None:
        context = Context()

    location = userdir() if asuser else "/etc/systemd/system"
    opt = "--user" if asuser else ""

    if sudo is None:
        if not asuser:
            sudo = get_sudo(context, use_su)
        else:
            sudo = context.run

    assert sudo is not None
    failed = []
    for systemdfile in systemdfiles:
        service = split(systemdfile)[-1]
        exists = isfile(f"{location}/{service}")
        if (
            not exists
            or context.run(
                f"cmp {location}/{service} {systemdfile}", hide=True, warn=True
            ).failed
        ):
            if exists:
                click.secho(f"warning: overwriting old {service}", fg="yellow")

                if sudo(f"systemctl {opt} stop {service}", warn=True).failed:
                    click.secho(
                        "failed to stop old process [already stopped?]",
                        fg="yellow",
                        err=True,
                    )
            sudo(f"cp {systemdfile} {location}")
            sudo(f"systemctl {opt} daemon-reload")
            sudo(f"systemctl {opt} enable {service}")
            sudo(f"systemctl {opt} start {service}")
            if sudo(f"systemctl {opt} status {service}", warn=True, hide=False).failed:
                sudo(f"systemctl {opt} disable {service}", warn=True)
                sudo(f"rm {location}/{service}")
                sudo(f"systemctl {opt} daemon-reload")
                click.secho("systemd configuration faulty", fg="red", err=True)
                failed.append(systemdfile)

        else:
            click.secho(f"systemd file {service} unchanged", fg="green")
    return failed


def nginx_install(
    nginxfile: str,
    context: Context | None = None,
    sudo: SUDO | None = None,
    use_su: bool = False,
) -> str | None:
    from invoke import Context  # pylint: disable=redefined-outer-name

    from .config import NGINX_DIRS

    if context is None:
        context = Context()

    conf = split(nginxfile)[-1]
    # Ubuntu, RHEL8
    for targetd in NGINX_DIRS:
        if isdir(targetd):
            break
    else:
        raise RuntimeError("can't find nginx configuration directory")
    if sudo is None:
        sudo = get_sudo(context, use_su)
    exists = isfile(f"{targetd}/{conf}")
    if (
        not exists
        or context.run(f"cmp {targetd}/{conf} {nginxfile}", hide=True, warn=True).failed
    ):
        if exists:
            click.secho(f"warning: overwriting old {conf}", fg="yellow")
        sudo(f"cp {nginxfile} {targetd}/")

        if sudo("nginx -t", warn=True).failed:

            sudo(f"rm {targetd}/{conf}")
            click.secho("nginx configuration faulty", fg="red", err=True)
            return None

        sudo("systemctl restart nginx")
    else:
        click.secho(f"nginx file {conf} unchanged", fg="green")
    return conf


def systemd_uninstall(
    systemdfiles: list[str],
    context: Context | None = None,
    sudo: SUDO | None = None,
    asuser: bool = False,
    use_su: bool = False,
) -> list[str]:

    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import userdir

    # install systemd file
    location = userdir() if asuser else "/etc/systemd/system"
    opt = "--user" if asuser else ""
    if context is None:
        context = Context()
    if sudo is None:
        if not asuser:
            sudo = get_sudo(context, use_su)
        else:
            sudo = context.run
    failed = []
    changed = False
    for sdfile in systemdfiles:
        systemdfile = split(sdfile)[-1]
        if "." not in systemdfile:
            systemdfile += ".service"
        if not isfile(f"{location}/{systemdfile}"):
            click.secho(f"no systemd service {systemdfile}", fg="yellow", err=True)
        else:
            r = sudo(f"systemctl {opt} stop {systemdfile}", warn=True)
            if r.failed and r.return_code != 5:
                failed.append(sdfile)
            if r.ok:
                sudo(f"systemctl {opt} disable {systemdfile}")
                sudo(f"rm {location}/{systemdfile}")
                changed = True
    if changed:
        sudo(f"systemctl {opt} daemon-reload")
    return failed


def nginx_uninstall(
    nginxfile: str,
    context: Context | None = None,
    sudo: SUDO | None = None,
    use_su: bool = False,
) -> None:
    from invoke import Context  # pylint: disable=redefined-outer-name

    from .config import NGINX_DIRS

    if sudo is None:
        if context is None:
            context = Context()
        sudo = get_sudo(context, use_su)

    nginxfile = split(nginxfile)[-1]
    if "." not in nginxfile:
        nginxfile += ".conf"

    for d in NGINX_DIRS:
        fname = join(d, nginxfile)
        if isfile(fname):
            sudo(f"rm {fname}")
            sudo("systemctl restart nginx")
            return

    click.secho(f"no nginx file {nginxfile}", fg="yellow", err=True)


SYSTEMD_ARGS = {
    "application_dir": "locations of repo",
    "appname": "application name [default: directory name]",
    "user": "user to run as [default: current user]",
    "group": "group for executable [default: current user's group]",
    "venv": "virtual environment to use [default: {application_dir}/../venv]",
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

    from jinja2 import UndefinedError

    if help_args is None:
        help_args = SYSTEMD_ARGS

    application_dir = topath(application_dir)

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    template = get_template(template, application_dir)

    known = (
        get_known(help_args)
        | {"app", "asuser"}
        | (set(extra_params.keys()) if extra_params else set())
    )
    defaults = [
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
        ]
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

        def isint(s: str | int):
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
                        f"unknown arguments {extra}", param_hint="params"
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
        click.secho(e.message, fg="red", bold=True, err=True)
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

            with maybe_closing(open(name, "wt") if name else None) as fp:
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
                    convert=dict(venv=topath, application_dir=topath),
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
    key: str, func: Callable[[Any], bool], msg: str
) -> tuple[str, CHECKTYPE]:
    def f(k, val) -> str | None:
        if func(val):
            return None
        return msg.format(key=val)

    return (key, f)


def to_output(res: str, output: str | TextIO | None = None) -> None:
    if output:
        if isinstance(output, str):
            with open(output, "wt") as fp:
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
) -> str:
    """Generate an nginx configuration for application"""
    from jinja2 import UndefinedError

    if args is None:
        args = []
    if application_dir is None and app is not None:
        application_dir = os.path.dirname(app.root_path)

    if help_args is None:
        help_args = NGINX_ARGS

    if convert is None:
        convert = dict(root=topath)
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
        defaults = [
            ("application_dir", lambda _: application_dir),
            ("appname", lambda params: split(params["application_dir"])[-1]),
            ("root", lambda _: staticdirs[0].folder),
            ("server_name", lambda _: server_name),
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
                        f"unknown arguments {extra}", param_hint="params"
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
        click.secho(e.message, fg="red", bold=True, err=True)
        raise click.Abort()


def config_options(f: F) -> F:
    f = click.option(
        "-o", "--output", help="write to this file", type=click.Path(dir_okay=False)
    )(f)
    f = click.option("-n", "--no-check", is_flag=True, help="don't check parameters")(f)
    return f


def su(f):
    return click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")(
        f
    )


def asuser_option(f):
    return click.option("-u", "--user", "asuser", is_flag=True, help="Install as user")(
        f
    )


def template_option(f):
    return click.option(
        "-t",
        "--template",
        metavar="TEMPLATE_FILE",
        help="template file or directory of templates",
    )(f)


@cli.group(help=click.style("nginx/systemd configuration commands", fg="magenta"))
def config():
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
        convert=dict(keyfile=topath),
    )


@config.command(name="template")
@asuser_option
@click.option(
    "-o", "--output", help="write to this file", type=click.Path(dir_okay=False)
)
@click.argument(
    "template", type=click.Path(exists=True, dir_okay=False, file_okay=True)
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
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
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
def run_nginx_app(application_dir, port, no_start_app=False, browse=False):
    """Run nginx as a non daemon process with web app in background."""
    import signal
    import uuid

    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import Runner, browser

    if application_dir is None:
        application_dir = "."

    application_dir = topath(application_dir)
    template = get_template("nginx-test.conf", application_dir)

    res = template.render(application_dir=application_dir, port=port)

    tmpfile = f"/tmp/nginx-{uuid.uuid4()}.conf"
    pidfile = tmpfile + ".pid"

    app = get_app_entrypoint(application_dir, "app.app")

    # procs = [Runner("nginx", f"nginx -c {tmpfile}", directory=application_dir)]
    procs = []
    url = f"http://127.0.0.1:{port}"
    click.secho(f"listening on {url}", fg="green", bold=True)

    if not no_start_app:
        venv = get_default_venv(application_dir)
        if os.path.isdir("venv"):
            gunicorn = os.path.join(venv, "bin", "gunicorn")
        else:
            gunicorn = "gunicorn"

        bgapp = Runner(
            app,
            f"{gunicorn} --pid {pidfile} --bind unix:app.sock {app}",
            directory=application_dir,
            pty=True,
        )
        procs.append(bgapp)
    else:
        click.secho(
            f"expecting app: cd {application_dir} && gunicorn --bind unix:app.sock {app}",
            fg="magenta",
            bold=True,
        )
    try:
        with open(tmpfile, "w") as fp:
            fp.write(res)
        threads = [b.start() for b in procs]
        if browse:
            threads.append(browser(url=url))
        try:
            Context().run(f"nginx -c {tmpfile}", pty=True)
        finally:
            if not no_start_app:
                with open(pidfile) as fp:
                    pid = int(fp.read().strip())
                    os.kill(pid, signal.SIGINT)

            for thrd in threads:

                thrd.join(timeout=2.0)

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
@click.argument("nginxfile", type=click.File())
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
def run_nginx_conf(nginxfile, application_dir, port, browse, venv):
    """Run nginx as a non daemon process using generated app config file."""
    import signal
    import threading
    from tempfile import NamedTemporaryFile

    # import uuid
    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import browser

    def once(m):
        done = False

        def f(r):
            nonlocal done
            if done:
                return ""
            done = True
            return m

        return f

    def get_server():
        """parse nginx.conf file for server and host"""

        def tohost(h):
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

    template = get_template("nginx-app.conf", application_dir)
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
                target=run_app, args=[application_dir, bind, venv, pidfile]
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
            Context().run(f"nginx -c {fp.name}", pty=True)
        finally:
            if thrd:
                with open(pidfile) as fp:
                    pid = int(fp.read().strip())
                    os.kill(pid, signal.SIGINT)
            for thrd in threads:
                thrd.join(timeout=2.0)
            rmfiles([pidfile])
        os.system("stty sane")


@config.command(name="nginx-install")
@su
@click.argument(
    "nginxfile", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
def nginx_install_cmd(nginxfile: str, use_su: bool) -> None:
    """Install nginx config file."""

    # install frontend
    conf = nginx_install(nginxfile, use_su=use_su)
    if conf is None:
        raise click.Abort()

    click.secho(f"{conf} installed!", fg="green", bold=True)


@config.command(name="nginx-uninstall")
@su
@click.argument("nginxfile")
def nginx_uninstall_cmd(nginxfile: str, use_su: bool) -> None:
    """Uninstall nginx config file."""

    nginx_uninstall(nginxfile, use_su=use_su)

    click.secho(f"{nginxfile} uninstalled!", fg="green", bold=True)


@config.command(name="systemd-install")
@asuser_option
@su
@click.argument(
    "systemdfiles",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
    nargs=-1,
    required=True,
)
def systemd_install_cmd(systemdfiles: list[str], use_su: bool, asuser: bool):
    """Install systemd files."""

    failed = systemd_install(systemdfiles, asuser=asuser, use_su=use_su)

    if failed:
        raise click.Abort()


@config.command(name="systemd-uninstall")
@asuser_option
@su
@click.argument(
    "systemdfiles",
    # type=click.Path(exists=True, dir_okay=False, file_okay=True),
    nargs=-1,
    required=True,
)
def systemd_uninstall_cmd(systemdfiles: list[str], use_su: bool, asuser: bool):
    """Uninstall systemd files."""

    failed = systemd_uninstall(systemdfiles, asuser=asuser, use_su=use_su)
    if failed:
        click.secho(f'failed to stop: {",".join(failed)}', fg="red", err=True)
        raise click.Abort()
