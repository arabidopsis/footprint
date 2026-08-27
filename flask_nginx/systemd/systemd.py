from __future__ import annotations

import subprocess
import sys
from os.path import isdir, isfile, split
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import click

from ..templating import get_template, undefined_error
from ..utils import get_app_entrypoint, get_variables, gethomedir, topath, userdir, which
from .cli import config
from .utils import (
    CHECKTYPE,
    CONVERTER,
    asgi_option,
    asuser_option,
    check_app_dir,
    check_user,
    config_options,
    fix_params,
    footprint_config,
    get_known,
    getgroup,
    getuser,
    ignore_unknowns_option,
    make_args,
    python_executable_option,
    template_option,
    to_check_func,
    to_output,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from jinja2 import Template


def systemd_install(
    systemdfiles: list[str],  # list of systemd unit files
    *,
    asuser: bool = False,  # install as user
) -> list[Path]:  # this of failed installations
    import filecmp

    location = userdir() if asuser else Path("/etc/systemd/system")

    sudo = which("sudo")
    systemctl = which("systemctl")

    def sudocmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        if not asuser:
            return subprocess.run([sudo, *args], check=check)
        return subprocess.run(list(args), check=check)

    def systemctlcmd(*args: str, check: bool = True) -> int:
        if not asuser:
            return subprocess.run(
                [sudo, systemctl, *args],
                check=check,
            ).returncode
        return subprocess.run(
            [systemctl, "--user", *args],
            check=check,
        ).returncode

    failed: list[Path] = []
    for _systemdfile in systemdfiles:
        systemdfile = Path(_systemdfile)
        service = systemdfile.name
        service_file = location / service
        exists = service_file.is_file()
        if not exists or not filecmp.cmp(service_file, systemdfile):
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
            sudocmd("cp", str(systemdfile), str(location))
            systemctlcmd("daemon-reload")
            systemctlcmd("enable", service)
            systemctlcmd("start", service)
            if systemctlcmd("status", service):
                systemctlcmd("disable", service, check=False)
                sudocmd("rm", str(service_file))
                systemctlcmd("daemon-reload")

                click.secho("systemd configuration faulty", fg="red", err=True)
                failed.append(systemdfile)

        else:
            click.secho(f"systemd file {service} unchanged", fg="green")
    return failed


def systemd_uninstall(  # noqa: C901
    systemdfiles: list[str],
    *,
    asuser: bool = False,
) -> list[Path]:
    # install systemd file
    location = userdir() if asuser else Path("/etc/systemd/system")
    sudo = which("sudo")
    systemctl = which("systemctl")

    def sudocmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        if not asuser:
            return subprocess.run([sudo, *args], check=check)
        return subprocess.run(list(args), check=check)

    def systemctlcmd(*args: str, check: bool = True) -> int:
        if not asuser:
            return subprocess.run(
                [sudo, systemctl, *args],
                check=check,
            ).returncode
        return subprocess.run(
            [systemctl, "--user", *args],
            check=check,
        ).returncode

    failed: list[Path] = []
    changed = False
    for _sdfile in systemdfiles:
        sdfile = Path(_sdfile)
        systemdfile = sdfile.name
        if "." not in systemdfile:
            systemdfile += ".service"
        filename = location / systemdfile
        if not filename.is_file():
            click.secho(f"no systemd service {systemdfile}", fg="yellow", err=True)
        else:
            ret = systemctlcmd("stop", str(systemdfile), check=False)
            if ret not in {0, 5}:
                failed.append(sdfile)
            if ret == 0:
                systemctlcmd("disable", systemdfile)
                sudocmd("rm", str(filename))
                changed = True
    if changed:
        systemctlcmd("daemon-reload")
    return failed


SYSTEMD_ARGS = {
    "application_dir": "locations of repo",
    "appname": "application name [default: directory name]",
    "user": "user to run as [default: current user]",
    "group": "group for executable [default: current user's group]",
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


def systemd(  # noqa: C901, PLR0915, PLR0912
    template: str | Path | Template,
    application_dir: Path | None,
    args: list[str] | None = None,
    *,
    help_args: dict[str, str] | None = None,
    check: bool = True,
    output: str | Path | IO[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    checks: list[tuple[str, CHECKTYPE]] | None = None,
    asuser: bool = False,
    ignore_unknowns: bool = False,
    default_values: list[tuple[str, CONVERTER]] | None = None,
    convert: dict[str, Callable[[Any], Any]] | None = None,
    asgi: bool = False,
    python_executable: str | None = None,
) -> str:
    # see https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
    # place this in /etc/systemd/system/
    from multiprocessing import cpu_count

    from jinja2 import UndefinedError

    if help_args is None:
        help_args = SYSTEMD_ARGS

    application_dir = topath(application_dir) if application_dir else Path.cwd()

    template = get_template(template, application_dir)
    variables = get_variables(template)
    known: set[str] = (
        get_known(help_args) | {"app", "asuser", "asgi"} | (set(extra_params.keys()) if extra_params else set())
    )
    known.update(variables)
    pe = Path(python_executable).absolute() if python_executable else Path(sys.executable)
    defaults: list[tuple[str, CONVERTER]] = [
        ("application_dir", lambda _: application_dir),
        ("asgi", lambda _: asgi),
        ("user", lambda _: getuser()),
        ("group", lambda params: getgroup(params["user"])),
        ("appname", lambda params: split(params["application_dir"])[-1]),
        ("homedir", lambda params: gethomedir(params["user"])),
        ("executable", lambda _: str(pe)),
        ("venv", lambda _: pe.parent.parent),
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
        params = {k: v for k, v in footprint_config(application_dir).items() if k in known}
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

        if "host" in params and "host" in known:
            h = params["host"]
            if isint(h):
                params["host"] = "127.0.0.1"
                params["port"] = h
            elif ":" in h:
                s, h = h.rsplit(":", maxsplit=1)
                params["host"] = s
                params["port"] = h

        if "port" not in params and "port" in known:
            params["port"] = 8000

        if check:
            if not ignore_unknowns:
                extra = set(params) - known
                if extra:
                    emsg = f"unknown arguments {extra}"
                    raise click.BadParameter(
                        emsg,
                        param_hint="params",
                    )
            failed: list[str] = []
            checks = [
                *(checks or []),
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
                    raise click.Abort

        if "asuser" not in params:
            params["asuser"] = asuser
        if "asgi" not in params:
            params["asgi"] = asgi
        if "app" not in params:
            app = get_app_entrypoint(application_dir)
            if ":" not in app:
                app += ":application"
            params["app"] = app
        res = template.render(**params)
        to_output(res, output)
    except UndefinedError as e:
        undefined_error(e, template, params)
        raise click.Abort from e
    return res


@config.command(name="systemd", help=SYSTEMD_HELP)
@asuser_option
@ignore_unknowns_option
@template_option
@config_options
@click.option(
    "-d",
    "--app-dir",
    "application_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False, path_type=Path),
    help="""location of repo or current directory""",
)
@asgi_option
@python_executable_option
@click.argument("params", nargs=-1)
def systemd_cmd(
    application_dir: Path | None,
    params: list[str],
    template: str | None,
    output: str | None,
    *,
    no_check: bool,
    asuser: bool,
    asgi: bool,
    ignore_unknowns: bool,
    python_executable: str | None,
) -> None:
    """Generate a systemd unit file to start gunicorn or uvicorn for this webapp.

    PARAMS are key=value arguments for the template.
    """
    from ..utils import has_mod

    if asgi:
        if not has_mod("uvicorn", python_executable):
            click.secho(
                "uvicorn is not installed. Please install it (or maybe you don't want to use --asgi?).",
                err=True,
                fg="red",
            )
            raise click.Abort
    elif not has_mod("gunicorn", python_executable):
        click.secho(
            "gunicorn is not installed. Please install it (or maybe you want to use --asgi?).",
            err=True,
            fg="red",
        )
        raise click.Abort

    systemd(
        template or ("uvicorn.service" if asgi else "systemd.service"),
        application_dir,
        params,
        help_args=SYSTEMD_ARGS,
        check=not no_check,
        output=output,
        asuser=asuser,
        asgi=asgi,
        ignore_unknowns=ignore_unknowns,
        checks=[
            ("application_dir", lambda _, v: check_app_dir(v)),
        ],
        convert={"venv": topath, "application_dir": topath},
        python_executable=python_executable,
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


@config.command(name="ssh-tunnel", help=TUNNEL_HELP, hidden=True)
@asuser_option
@ignore_unknowns_option
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
    output: str | None,
    *,
    no_check: bool,
    asuser: bool,
    ignore_unknowns: bool,
) -> None:
    """Generate a systemd unit file to start ssh tunnel to TARGET.

    PARAMS are key=value arguments for the template.
    """
    systemd(
        template or "secure-tunnel.service",
        Path.cwd(),
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
                lambda _, f: None if isfile(f) else f'keyfile "{f}" is not a file',  # noqa: PTH113
            ),
            (
                "restart",
                lambda _, n: None if n > 2 else "restart {n} is too short an interval",  # noqa: PLR2004
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
    type=click.Path(dir_okay=False, file_okay=True, path_type=Path),
)
@click.argument("template", type=click.Path(exists=True, dir_okay=False, file_okay=True, path_type=Path), required=True)
@click.argument("params", nargs=-1)
def template_cmd(
    params: list[str],
    template: Path,
    output: Path | None,
    *,
    asuser: bool,
) -> None:
    """Generate file from a jinja template.

    PARAMS are key=value arguments for the template.
    """
    systemd(
        template,
        Path.cwd(),
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
def systemd_install_cmd(systemdfiles: list[str], *, asuser: bool) -> None:
    """Install systemd files."""
    check_user(asuser=asuser)

    failed = systemd_install(systemdfiles, asuser=asuser)

    if failed:
        raise click.Abort


@config.command(name="systemd-uninstall")
@asuser_option
@click.argument(
    "systemdfiles",
    nargs=-1,
    required=True,
)
def systemd_uninstall_cmd(systemdfiles: list[str], *, asuser: bool) -> None:
    """Uninstall systemd files."""
    check_user(asuser=asuser)
    failed = systemd_uninstall(systemdfiles, asuser=asuser)
    if failed:
        click.secho(
            f"failed to stop: {','.join([f.name for f in failed])}",
            fg="red",
            err=True,
        )
        raise click.Abort
