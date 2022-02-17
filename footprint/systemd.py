import os
import re
import typing as t
from contextlib import redirect_stderr
from io import StringIO
from os.path import isdir, isfile, join, split

import click
from jinja2 import UndefinedError

from .cli import cli
from .templating import get_template, topath
from .utils import SUDO, gethomedir, rmfiles

if t.TYPE_CHECKING:
    from flask import Flask  # pylint: disable=unused-import
    from invoke import Context  # pylint: disable=unused-import

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def fix_kv(key: str, *values: str) -> t.Tuple[str, t.Any]:
    # if key in {"gevent"}:  # boolean flag
    #     return ("gevent", True)
    if "" in values:
        raise UndefinedError(f"no value for {key}")
    key = key.replace("-", "_")
    if not values:  # simple key is True
        return (key, True)
    value = "=".join(values)
    if value.isdigit():
        return (key, int(value))
    if value == "true":
        return (key, True)
    if value == "false":
        return (key, False)
    if NUM.match(value):
        return (key, float(value))
    return (key, value)


def fix_params(params: t.List[str]) -> t.Dict[str, t.Any]:
    return dict(fix_kv(*p.split("=")) for p in params)


KW = re.compile(r"^([\w_-]+)\s*:", re.M)


def get_known(help_str: str) -> t.Set[str]:
    # assumes help_string is """text\b args \b more text"
    # and args is of the form "keyword : some text"
    parts = help_str.split("\b")
    if len(parts) > 1:
        part = parts[1]
    else:
        part = parts[0]
    return {
        s.replace("-", "_")
        for s in KW.findall("\n".join(s.strip() for s in part.splitlines()))
    }


def url_match(directory: str, exclude=None) -> str:

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


STATIC_RULE = re.compile("^(.*)/<path:filename>$")


def find_favicon(application_dir: str) -> t.Optional[str]:

    for d, _, files in os.walk(application_dir):
        if d.startswith((".", "_")):
            continue
        for f in files:
            if f in {"favicon.ico", "robots.txt"}:
                return d
    return None


def find_application(application_dir: str, module: str) -> "Flask":
    import sys
    from importlib import import_module

    remove = False
    if application_dir not in sys.path:
        sys.path.append(application_dir)
        remove = True
    try:

        # FIXME: we really want to run this
        # under the virtual environment that this pertains too
        venv = sys.prefix
        click.secho(
            f"trying to load application ({module}) using {venv}: ",
            fg="yellow",
            nl=False,
            err=True,
        )
        with redirect_stderr(StringIO()) as stderr:
            m = import_module(module)
            app = m.application  # type: ignore
        v = stderr.getvalue()
        if v:
            click.secho(f"got errors ...{click.style(v[-100:], fg='red')}", err=True)
        else:
            click.secho("ok", fg="green", err=True)
        return t.cast("Flask", app)
    except (ImportError, AttributeError) as e:
        raise click.BadParameter(
            f"can't load application from {application_dir}: {e}"
        ) from e
    finally:
        if remove:
            sys.path.remove(application_dir)


class StaticFolder(t.NamedTuple):
    url: t.Optional[str]
    folder: str
    rewrite: bool


def get_static_folders(app: "Flask") -> t.List[StaticFolder]:  # noqa: C901
    def get_static_folder(rule):
        bound_method = app.view_functions[rule.endpoint]
        if hasattr(bound_method, "static_folder"):
            return bound_method.static_folder
        # __self__ is the blueprint of send_static_file method
        if hasattr(bound_method, "__self__"):
            bp = bound_method.__self__
            if bp.has_static_folder:
                return bp.static_folder
        # now just a lambda :(
        return None

    def find_static(app: "Flask") -> t.Iterator[StaticFolder]:
        if app.has_static_folder:
            prefix, folder = app.static_url_path, app.static_folder
            if folder is not None and isdir(folder):
                yield StaticFolder(
                    prefix,
                    topath(folder),
                    (not folder.endswith(prefix) if prefix else False),
                )
        for r in app.url_map.iter_rules():
            if not r.endpoint.endswith("static"):
                continue
            m = STATIC_RULE.match(r.rule)
            if not m:
                continue
            rewrite = False
            prefix = m.group(1)
            folder = get_static_folder(r)
            if folder is None:
                if r.endpoint != "static":
                    # static view_func for app is now
                    # just a lambda.
                    click.secho(
                        f"location: can't find static folder for endpoint: {r.endpoint}",
                        fg="red",
                        err=True,
                    )
                continue
            if not folder.endswith(prefix):
                rewrite = True

            if not isdir(folder):
                continue
            yield StaticFolder(prefix, topath(folder), rewrite)

    return list(set(find_static(app)))


def check_app_dir(application_dir: str) -> None:
    if not isdir(application_dir):
        raise click.BadParameter(
            f"not a directory: {application_dir}",
            param_hint="application_dir",
        )


def check_venv_dir(venv_dir: str) -> None:

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


def footprint_config(application_dir: str) -> t.Dict[str, t.Any]:
    import types

    from dotenv import dotenv_values

    def dot_env(f: str):
        cfg = dotenv_values(f)
        return dict(
            fix_kv(k.lower(), v)
            for k, v in cfg.items()
            if k.isupper() and v is not None
        )

    def module_cfg(f: str):
        with open(f, "rb") as fp:
            d = types.ModuleType("config")
            d.__file__ = f
            exec(  # pylint: disable=exec-used
                compile(fp.read(), f, mode="exec"), d.__dict__
            )
            return dict(fix_kv(k.lower(), getattr(d, k)) for k in dir(d) if k.isupper())

    f = join(application_dir, ".flaskenv")
    if not isfile(f):
        return {}
    return dot_env(f)


def get_default_venv(application_dir: str) -> str:
    return topath(join(application_dir, "..", "venv"))


def run_app(
    application_dir: str,
    bind: t.Optional[str] = None,
    venv: t.Optional[str] = None,
    pidfile: t.Optional[str] = None,
    app: str = "app.app",
) -> None:
    from invoke import Context  # pylint: disable=redefined-outer-name

    if pidfile is None:
        pidfile = "/tmp/gunicorn.pid"

    if venv is None:
        venv = get_default_venv(application_dir)
    check_venv_dir(venv)
    c = Context()
    with c.cd(application_dir):
        bind = bind if bind else "unix:app.sock"
        cmd = f"{venv}/bin/gunicorn  --pid {pidfile} --access-logfile=- --error-logfile=- --bind {bind} {app}"
        click.secho(
            f"starting gunicorn in {topath(application_dir)}", fg="green", bold=True
        )
        click.secho(cmd, fg="green")
        c.run(cmd, pty=True)


def config_options(f: F) -> F:
    f = click.option(
        "-o", "--output", help="write to this file", type=click.Path(dir_okay=False)
    )(f)
    f = click.option("-n", "--no-check", is_flag=True, help="don't check parameters")(f)
    return f


def systemd_install(
    systemdfiles: t.List[str],
    c: t.Optional["Context"] = None,
    sudo: t.Optional[SUDO] = None,
    asuser: bool = False,
    use_su: bool = False,
) -> t.List[str]:

    # install systemd file
    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import sudoresponder, suresponder

    if c is None:
        c = Context()

    location = (
        os.path.expanduser("~/.config/systemd/user")
        if asuser
        else "/ext/systemd/system"
    )
    opt = "--user" if asuser else ""

    c = Context()
    if sudo is None:
        if not asuser:
            sudo = (
                sudoresponder(c, lazy=True) if not use_su else suresponder(c, lazy=True)
            )
        else:
            sudo = c.run
    assert sudo is not None
    failed = []
    for systemdfile in systemdfiles:
        service = split(systemdfile)[-1]
        exists = isfile(f"{location}/{service}")
        if (
            not exists
            or c.run(
                f"cmp {location}/{service} {systemdfile}", hide=True, warn=True
            ).failed
        ):
            if exists:
                click.secho(f"warning: overwriting old {service}", fg="yellow")
            sudo(f"cp {systemdfile} {location}")
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


def nginx_install(nginxfile: str, c: "Context", sudo: SUDO) -> t.Optional[str]:
    from .config import NGINX_DIRS

    conf = split(nginxfile)[-1]
    # Ubuntu, RHEL8
    for targetd in NGINX_DIRS:
        if isdir(targetd):
            break
    else:
        raise RuntimeError("can't find nginx configuration directory")

    exists = isfile(f"{targetd}/{conf}")
    if (
        not exists
        or c.run(f"cmp {targetd}/{conf} {nginxfile}", hide=True, warn=True).failed
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
    systemdfiles: t.List[str],
    sudo: t.Optional[SUDO] = None,
    asuser: bool = False,
    use_su: bool = False,
) -> t.List[str]:

    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import sudoresponder, suresponder

    # install systemd file
    location = (
        os.path.expanduser("~/.config/systemd/user")
        if asuser
        else "/ext/systemd/system"
    )
    opt = "--user" if asuser else ""

    c = Context()
    if sudo is None:
        if not asuser:
            sudo = (
                sudoresponder(c, lazy=True) if not use_su else suresponder(c, lazy=True)
            )
        else:
            sudo = c.run
    failed = []
    for sdfile in systemdfiles:
        systemdfile = split(sdfile)[-1]
        if not isfile(f"{location}/{systemdfile}"):
            click.secho(f"no systemd service {systemdfile}", fg="yellow", err=True)
        else:
            r = sudo(f"systemctl {opt} stop {systemdfile}", warn=True)
            if r.failed and r.return_code != 5:
                failed.append(sdfile)
            if r.ok:
                sudo(f"systemctl {opt} disable {systemdfile}")
                sudo(f"rm {location}/{systemdfile}")
    sudo(f"systemctl {opt} daemon-reload")
    return failed


def nginx_uninstall(nginxfile: str, sudo: SUDO) -> None:
    nginxfile = split(nginxfile)[-1]
    from .config import NGINX_DIRS

    for d in NGINX_DIRS:
        fname = join(d, nginxfile)
        if isfile(fname):
            sudo(f"rm {fname}")
            sudo("systemctl restart nginx")
            return

    click.secho(f"no nginx file {nginxfile}", fg="yellow", err=True)


def has_error_page(static_folders: t.List[StaticFolder]) -> t.Optional[StaticFolder]:

    for s in static_folders:

        if "404.html" in os.listdir(s.folder):
            return s
    return None


SYSTEMD_HELP = """
    Generate a systemd unit file for a website.

    Use footprint config systemd /var/www/websites/repo ... etc.
    with the following arguments:

    \b
    application_dir : locations of repo
    appname         : application name [default: directory name]
    user            : user to run as [default: current user]
    group           : group for executable [default: current user's group]
    venv            : virtual environment to use [default: {application_dir}/../venv]
    workers         : number of gunicorn workers [default: (CPU*2+1)]
    stopwait        : seconds to wait for website to stop
    after           : start after this service [default: mysql.service]
    host            : bind gunicorn to a port [default: use unix socket]
    asuser          : systemd destined for --user directory
    miniconda       : minconda *bin* directory
    homedir         : $HOME (default generated from user parameter)
    \b
    example:
    \b
    footprint config systemd /var/www/website3/mc_msms host=8001
"""


CHECKTYPE = t.Callable[[str, t.Any], t.Optional[str]]


def getgroup(username: str) -> t.Optional[str]:
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


# pylint: disable=too-many-branches too-many-locals
def systemd(  # noqa: C901
    template_name: str,
    application_dir: str,
    args: t.Optional[t.List[str]] = None,
    help_str: str = SYSTEMD_HELP,
    check: bool = True,
    output: t.Optional[t.Union[str, t.TextIO]] = None,
    extra_params: t.Optional[t.Dict[str, t.Any]] = None,
    checks: t.Optional[t.List[t.Tuple[str, CHECKTYPE]]] = None,
    asuser: bool = False,
    ignore_unknowns: bool = False,
    default_values=None,
):
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    import getpass
    from multiprocessing import cpu_count

    application_dir = topath(application_dir)

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    template = get_template(template_name, application_dir)

    known = (
        get_known(help_str)
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
        ("workers", lambda _: cpu_count() * 2 + 1),
        ("homedir", lambda params: gethomedir(params["user"])),
    ] + list(default_values or [])
    try:
        params = {
            k: v for k, v in footprint_config(application_dir).items() if k in known
        }
        params.update(fix_params(args or []))
        if extra_params:
            params.update(extra_params)

        for key, default_func in defaults:
            if key not in params:
                v = default_func(params)
                if v is not None:
                    params[key] = v
                    known.add(key)

        if "host" in params:
            h = params["host"]
            if isinstance(h, int) or h.isdigit():
                params["host"] = f"0.0.0.0:{h}"
        # params.setdefault("gevent", False)

        if check:

            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}", param_hint="params"
                    )
            failed = []
            checks = list(checks or [])
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

        if "asuser" not in params:
            params["asuser"] = asuser
        if "app" not in params:
            params["app"] = os.environ.get("FLASK_APP", "app.app")
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


NGINX_HELP = """
    Generate a nginx conf file for website.

    Use footprint config nginx /var/www/websites/repo website ... etc.
    with the following arguments:

    \b
    server_name         : name of website
    application_dir     : locations of repo
    appname             : application name [default: directory name]
    root                : static files root directory
    root_prefix         : location prefix to use (only used if root is defined)
    prefix              : url prefix for application [default: /]
    expires             : expires header for static files [default: off] e.g. 30d
    listen              : listen on port [default: 80]
    host                : proxy to a port [default: use unix socket]
    root_location_match : regex for matching static directory files
    access_log          : 'on' or 'off'. log static asset requests [default:off]
    extra               : extra (legal) nginx commands for proxy
    \b
    example:
    \b
    footprint config nginx /var/www/website3/mc_msms mcms.plantenergy.edu.au access-log=on
"""


def nginx(  # noqa: C901
    application_dir: t.Optional[str],
    server_name: str,
    args: t.Optional[t.List[str]] = None,
    *,
    app: t.Optional["Flask"] = None,
    template_name: t.Optional["str"] = None,
    help_str: str = NGINX_HELP,
    check: bool = True,
    output: t.Optional[t.Union[str, t.TextIO]] = None,
    extra_params: t.Optional[t.Dict[str, t.Any]] = None,
    checks: t.Optional[t.List[t.Tuple[str, CHECKTYPE]]] = None,
    ignore_unknowns: bool = False,
) -> None:
    if args is None:
        args = []
    if application_dir is None and app is not None:
        application_dir = os.path.dirname(app.root_path)

    if app is None and application_dir is None:
        raise click.BadParameter("Either app or application_dir must be specified")
    assert application_dir is not None

    application_dir = topath(application_dir)
    template = get_template(template_name or "nginx.conf", application_dir)

    known = get_known(help_str) | {"staticdirs", "favicon", "error_page"}
    root_location_match = None
    try:
        params = {
            k: v for k, v in footprint_config(application_dir).items() if k in known
        }
        params.update(fix_params(args))
        if extra_params:
            params.update(extra_params)

        prefix = params.get("prefix", "")
        if "root" in params:
            root = topath(join(application_dir, params["root"]))
            rp = params.get("root_prefix", None)
            static = [StaticFolder(rp if rp is not None else prefix, root, False)]
            params["root"] = root
        else:
            static = []

        def fixstatic(s: StaticFolder):
            url = prefix + s.url
            if url and s.folder.endswith(url):
                path = s.folder[: -len(url)]
                return StaticFolder(url, path, False)
            return StaticFolder(url, s.folder, s.rewrite if not prefix else True)

        if app is None:
            app = find_application(
                application_dir, os.environ.get("FLASK_APP", "app.app")
            )
        static.extend([fixstatic(s) for s in get_static_folders(app)])

        error_page = has_error_page(static)
        if error_page:
            params["error_page"] = error_page
        params["staticdirs"] = static
        for s in static:
            if not s.url:
                root_location_match = url_match(s.folder)
        # need a root directory for server
        if "root" not in params and not static:
            raise click.BadParameter("no root directory found", param_hint="params")

        for key, f in [
            ("application_dir", lambda: application_dir),
            ("appname", lambda: split(params["application_dir"])[-1]),
            ("root", lambda: static[0][1]),
            ("server_name", lambda: server_name),
        ]:
            if key not in params:
                v = f()
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
            check_app_dir(application_dir)

            if not isdir(params["root"]):
                raise click.BadParameter(
                    f"not a directory: \"{params['root']}\"",
                    param_hint="params",
                )
            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}", param_hint="params"
                    )
            failed = []
            for key, func in checks or []:
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


def su(f):
    return click.option("--su", "use_su", is_flag=True, help="use su instead of sudo")(
        f
    )


def asuser(f):
    return click.option("-u", "--user", "asuser", is_flag=True, help="Install as user")(
        f
    )


@cli.group(help=click.style("nginx/systemd config commands", fg="magenta"))
def config():
    pass


@config.command(name="systemd", help=SYSTEMD_HELP)
@asuser
@click.option("-i", "--ignore-unknowns", is_flag=True, help="ignore unknown variables")
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@config_options
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
@click.argument("params", nargs=-1)
def systemd_cmd(
    application_dir: t.Optional[str],
    params: t.List[str],
    template: t.Optional[str],
    no_check: bool,
    output: t.Optional[str],
    asuser: bool,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to start gunicorn for this webapp.

    PARAMS are key=value arguments for the template.
    """
    systemd(
        template or "systemd.service",
        application_dir or ".",
        params,
        help_str=SYSTEMD_HELP,
        check=not no_check,
        output=output,
        asuser=asuser,
        ignore_unknowns=ignore_unknowns,
        checks=[
            ("application_dir", lambda _, v: check_app_dir(v)),
            ("venv", lambda _, v: check_venv_dir(v)),
        ],
    )


TUNNEL_HELP = """
    Generate a systemd unit file for a ssh tunnel.

    Use footprint config tunnel machine ... etc.
    with the following arguments:

    \b
    remote-user     : remote user to run as [default: current user]
    restart         : seconds to wait for before restart [default: 5]
    local-addr      : local address to connect [default: 127.0.0.1]
    local-port      : local port to connect to
    remote-port     : remote port to connect to
    keyfile         : ssh keyfile to use for target machine
    \b
    example:
    \b
    footprint config tunnel machine1 local-port=8001 remote-port=80
 """


@config.command(name="ssh-tunnel", help=TUNNEL_HELP)
@asuser
@click.option("-i", "--ignore-unknowns", is_flag=True, help="ignore unknown variables")
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@config_options
@click.argument(
    "target",
    required=True,
)
@click.argument("params", nargs=-1)
def tunnel_cmd(
    target: str,
    params: t.List[str],
    template: t.Optional[str],
    no_check: bool,
    output: t.Optional[str],
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
        help_str=TUNNEL_HELP,
        check=not no_check,
        output=output,
        asuser=asuser,
        extra_params={"target": target},
        ignore_unknowns=ignore_unknowns,
        checks=[
            (
                "keyfile",
                lambda _, f: None if isfile(f) else f'keyfile "{f}" is not a file',
            )
        ],
        default_values=[
            ("local_addr", lambda _: "127.0.0.1"),
            ("restart", lambda _: 5),
            ("remote-user", lambda params: params["user"]),
        ],
    )


@config.command(name="template")
@asuser
@click.option(
    "-o", "--output", help="write to this file", type=click.Path(dir_okay=False)
)
@click.argument(
    "template", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
@click.argument("params", nargs=-1)
def template_cmd(
    params: t.List[str],
    template: str,
    output: t.Optional[str],
    asuser: bool,
) -> None:
    """Generate file from a jinja template.

    PARAMS are key=value arguments for the template.
    """
    systemd(
        template,
        ".",
        params,
        help_str="",
        check=False,
        output=output,
        asuser=asuser,
        ignore_unknowns=True,
    )


# pylint: disable=too-many-locals too-many-branches
@config.command(name="nginx", help=NGINX_HELP)  # noqa: C901
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@config_options
@click.argument(
    "application_dir", type=click.Path(exists=True, dir_okay=True, file_okay=False)
)
@click.argument("server_name")
@click.argument("params", nargs=-1)
def nginx_cmd(
    application_dir: str,
    server_name: str,
    template: t.Optional[str],
    params: t.List[str],
    no_check: bool,
    output: t.Optional[str],
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
    app = os.environ.get("FLASK_APP", "app.app")

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
        rmfiles([tmpfile])
        os.system("stty sane")


@config.command()
@click.option(
    "-p",
    "--port",
    default=2048,
    help="port to listen",
)
@click.option("--browse", is_flag=True, help="open web application in browser")
@click.argument("nginxfile", type=click.File())
@click.argument(
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    required=False,
)
def run_nginx_conf(nginxfile, application_dir, port, browse):
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

        server = nginxfile.read()
        # remove old access_log and replace listen commands
        server = A.sub("", server)
        server = L.sub(once(f"listen {port};"), server)

        m = H.search(server)
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
                target=run_app, args=[application_dir, bind, None, pidfile]
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
        os.system("stty sane")


@config.command(name="nginx-install")
@asuser
@su
@click.argument(
    "nginxfile", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
def nginx_install_(nginxfile: str, use_su: bool, asuser: bool) -> None:
    """Install nginx config file."""
    # from .utils import suresponder
    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import sudoresponder, suresponder

    c = Context()
    sudo = sudoresponder(c, lazy=True) if not use_su else suresponder(c, lazy=True)
    # install frontend
    conf = nginx_install(nginxfile, c, sudo)
    if conf is None:
        raise click.Abort()

    click.secho(f"{conf} installed!", fg="green", bold=True)


@config.command(name="nginx-uninstall")
@asuser
@su
@click.argument(
    "nginxfile", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
def nginx_uninstall_(nginxfile: str, use_su: bool, asuser: bool) -> None:
    """Uninstall nginx config file."""

    from invoke import Context  # pylint: disable=redefined-outer-name

    from .utils import sudoresponder, suresponder

    c = Context()
    sudo = sudoresponder(c, lazy=True) if not use_su else suresponder(c, lazy=True)
    # remove from nginx first
    nginx_uninstall(nginxfile, sudo)

    click.secho(f"{nginxfile} uninstalled!", fg="green", bold=True)


@config.command(name="systemd-install")
@asuser
@su
@click.argument(
    "systemdfiles",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
    nargs=-1,
    required=True,
)
def systemd_install_cmd(systemdfiles: t.List[str], use_su: bool, asuser: bool):
    """Install systemd files."""

    failed = systemd_install(systemdfiles, asuser=asuser, use_su=use_su)

    if failed:
        raise click.Abort()


@config.command(name="systemd-uninstall")
@asuser
@su
@click.argument(
    "systemdfiles",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
    nargs=-1,
    required=True,
)
def systemd_uninstall_cmd(systemdfiles: t.List[str], use_su: bool, asuser: bool):
    """Uninstall systemd files."""

    failed = systemd_uninstall(systemdfiles, asuser=asuser, use_su=use_su)
    if failed:
        click.secho(f'failed to stop: {",".join(failed)}', fg="red", err=True)
        raise click.Abort()
