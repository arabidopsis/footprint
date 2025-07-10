from __future__ import annotations

import subprocess
import sys
from os.path import isdir
from os.path import isfile
from os.path import split
from typing import Any
from typing import Callable
from typing import TextIO
from typing import TYPE_CHECKING

import click

from ..core import get_app_entrypoint
from ..core import topath
from ..templating import get_template
from ..templating import undefined_error
from ..utils import get_variables
from ..utils import gethomedir
from ..utils import rmfiles
from ..utils import userdir
from ..utils import which
from .cli import config
from .utils import asgi_option
from .utils import asuser_option
from .utils import check_app_dir
from .utils import check_user
from .utils import check_venv_dir
from .utils import CHECKTYPE
from .utils import config_options
from .utils import CONVERTER
from .utils import fix_params
from .utils import footprint_config
from .utils import get_default_venv
from .utils import get_known
from .utils import getgroup
from .utils import getuser
from .utils import make_args
from .utils import template_option
from .utils import to_check_func
from .utils import to_output

if TYPE_CHECKING:
    from jinja2 import Template


def systemd_install(
    systemdfiles: list[str],  # list of systemd unit files
    asuser: bool = False,  # install as user
) -> list[str]:  # this of failed installations
    import filecmp

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

    failed: list[str] = []
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


def systemd_uninstall(
    systemdfiles: list[str],
    asuser: bool = False,
) -> list[str]:
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

    failed: list[str] = []
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


SYSTEMD_ARGS = {
    "application_dir": "locations of repo",
    "appname": "application name [default: directory name]",
    "user": "user to run as [default: current user]",
    "group": "group for executable [default: current user's group]",
    "venv": "virtual environment to use [default: {application_dir}/{.venv,../venv}]",
    "workers": "number of gunicorn workers [default: (CPU // 2 + 1) or 2 for ASGI]",
    "stopwait": "seconds to wait for website to stop",
    "after": "start after this service [default: mysql.service]",
    "host": "bind gunicorn to a port [default: use unix socket]",
    "asuser": "systemd destined for --user directory",
    "homedir": "$HOME (default generated from user parameter)",
    "executable": "defaults to sys.executable i.e. the current python",
    "path": "extra bin directories to add to PATH",
    "env-file": "path to a environment file",
}


SYSTEMD_HELP = f"""
Generate a systemd unit file for a website.

Use footprint config systemd ... etc.
with the following arguments:

\b
{make_args(SYSTEMD_ARGS)}
\b
example:
\b
footprint config systemd host=8001
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
    default_values: list[tuple[str, CONVERTER]] | None = None,
    convert: dict[str, Callable[[Any], Any]] | None = None,
    asgi: bool = False,
) -> str:
    # pylint: disable=line-too-long
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    from multiprocessing import cpu_count
    from jinja2 import UndefinedError

    if help_args is None:
        help_args = SYSTEMD_ARGS

    application_dir = topath(application_dir)

    # if not params:
    #     raise click.BadParameter("use --help for params", param_hint="params")
    template = get_template(template, application_dir)
    variables = get_variables(template)
    known: set[str] = (
        get_known(help_args)
        | {"app", "asuser", "asgi"}
        | (set(extra_params.keys()) if extra_params else set())
    )
    known.update(variables)
    defaults: list[tuple[str, CONVERTER]] = [
        ("application_dir", lambda _: application_dir),
        ("asgi", lambda _: asgi),
        ("user", lambda _: getuser()),
        ("group", lambda params: getgroup(params["user"])),
        ("appname", lambda params: split(params["application_dir"])[-1]),
        ("venv", lambda _: get_default_venv()),
        ("homedir", lambda params: gethomedir(params["user"])),
        ("executable", lambda _: sys.executable),
    ]
    if default_values:
        defaults.extend(default_values)
    defaults.extend(
        [
            ("workers", lambda _: 2 if asgi else cpu_count() // 2 + 1),
        ],
    )
    params = {}
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
                params["host"] = "127.0.0.1"
                params["port"] = h
            else:
                if ":" in h:
                    s, h = h.rsplit(":", maxsplit=1)
                    params["host"] = s
                    params["port"] = h

        if "port" not in params:
            params["port"] = 8000

        if check:
            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    raise click.BadParameter(
                        f"unknown arguments {extra}",
                        param_hint="params",
                    )
            failed: list[str] = []
            checks = list(checks or []) + [
                to_check_func("stopwait", isint, "{stopwait} is not an integer"),
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
        if "asgi" not in params:
            params["asgi"] = asgi
        if "app" not in params:
            app = get_app_entrypoint(application_dir, asgi=asgi)
            if asgi and ":" not in app:
                app += ":application"
            params["app"] = app
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
    asgi: bool = False,
) -> None:
    """Generate a systemd unit file to start gunicorn for this webapp.

    PARAMS are key=value arguments for the template.
    """
    from jinja2 import Template

    from ..templating import get_templates
    from ..utils import maybe_closing

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
    templates = get_templates(
        template or ("uvicorn.service" if asgi else "systemd.service"),
    )
    for tmpl in templates:
        name = None
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
                    asgi=asgi,
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


@config.command(name="systemd", help=SYSTEMD_HELP)
@asuser_option
@click.option("-i", "--ignore-unknowns", is_flag=True, help="ignore unknown variables")
@template_option
@config_options
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="""location of repo or current directory""",
)
@asgi_option
@click.argument("params", nargs=-1)
def systemd_cmd(
    application_dir: str | None,
    params: list[str],
    template: str | None,
    no_check: bool,
    output: str | None,
    asuser: bool,
    asgi: bool,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to start gunicorn or uvicorn for this webapp.

    PARAMS are key=value arguments for the template.
    """
    from ..utils import require_mod

    if asgi:
        require_mod("uvicorn")
    else:
        require_mod("gunicorn")

    multi_systemd(
        template,
        application_dir,
        params,
        check=not no_check,
        output=output,
        ignore_unknowns=ignore_unknowns,
        asuser=asuser,
        asgi=asgi,
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
