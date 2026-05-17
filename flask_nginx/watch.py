from __future__ import annotations

import re
import subprocess

import click

from .cli import Cli
from .cli import cli
from .cli import pass_config
from .utils import which


def vmemory_ok(threshold: int = 100) -> list[str]:
    import psutil

    from .utils import human

    m = psutil.virtual_memory()
    mn = threshold * 1024 * 1024  # megabyte
    if mn <= 0:
        return [f"memory available: {human(m.available)} ({m.percent}% used)"]

    if m.available < mn:
        return [f"low memory: {human(m.available)} < {human(mn)} ({m.percent}% used)"]
    return []


def disks_ok(threshold: int = 100) -> list[str]:
    import psutil

    from .utils import human

    mounts = [
        p.mountpoint
        for p in psutil.disk_partitions()
        if not p.device.startswith("/dev/loop") and not p.mountpoint.startswith("/boot")
    ]
    mn = threshold * 1024 * 1024  # megabytes

    ret: list[str] = []
    app = ret.append
    for m in mounts:
        du = psutil.disk_usage(m)
        if mn <= 0:
            app(f"partition {m}: {human(du.free)} Avail ({du.percent}% used)")
        elif du.free < mn:
            app(f"partition {m}: {human(du.free)} < {human(mn)} ({du.percent}% used)")
    return ret


# @cli.command()
# @watch_options
# @click.argument("email", required=False)
def run_watch(
    email: str | None,
    mem_threshold: int,
    disk_threshold: int,
    mailhost: str | None,
) -> None:
    import platform

    if disk_threshold > 0 and mem_threshold > 0:
        status = "Low memory"
    else:
        status = "Status"
    machine = platform.node()
    W = """<strong>{status} on {machine}</strong>:<br/>
{disk}"""
    memory = vmemory_ok(mem_threshold)
    disk = disks_ok(disk_threshold)

    if disk or memory:
        from .mailer import sendmail

        m = "<br/>\n".join(disk + memory)
        msg = W.format(disk=m, machine=machine, status=status)
        if email:
            sendmail(msg, email, mailhost=mailhost, subject=f"{status} on {machine}")
        else:
            click.echo(msg)


def add_cron_command(cmd: str, test_line: str | None = None) -> None:
    from tempfile import NamedTemporaryFile

    crontab = which("crontab")

    p = subprocess.run(
        [crontab, "-l"],
        capture_output=True,
        check=False,
        text=True,
    ).stdout

    ct = []
    added = False
    for line in p.splitlines():
        if test_line is None or re.search(test_line, line):
            ct.append(cmd)
            added = True
        else:
            ct.append(line)
    if not added:
        ct.append(cmd)

    with NamedTemporaryFile("wt") as fp:
        fp.write("\n".join(ct))
        fp.write("\n")
        fp.flush()
        # load new crontab
        subprocess.run([crontab, fp.name], check=True)


TME = re.compile("^([1-9][0-9]*)([mhd])$")


def make_cron_interval(tme: str) -> str:
    mtch = TME.match(tme)
    if mtch:
        i, k = mtch.group(1, 2)
        iv = int(i)
        if k == "m":
            if iv >= 60:
                raise click.BadParameter(
                    f'"{tme}" is not a minute interval',
                    param_hint="interval",
                )
            return f"*/{i} * * * *"
        if k == "h":
            if iv >= 24:
                raise click.BadParameter(
                    f'"{tme}" is not a hour interval',
                    param_hint="interval",
                )
            return f"0 */{i} * * *"
        else:
            if iv >= 32:
                raise click.BadParameter(
                    f'"{tme}" is not a day interval',
                    param_hint="interval",
                )
            return f"0 0 */{i} * *"

    if not tme.isdigit():
        return tme
    interval_mins = int(tme)
    if interval_mins < 60:
        tme = f"*/{interval_mins} * * * *"
    else:
        h = interval_mins // 60
        m = interval_mins - h * 60
        if h < 24:
            tme = f"{m} */{h} * * *"
        else:
            day = h // 24
            h = h - day
            if day < 32:
                tme = f"{m} {h} */{day} * *"
            else:
                mon = day // 32
                day = mon - day
                if mon < 12:
                    tme = f"{m} {h} {day} */{mon} *"
                else:
                    raise click.BadParameter(
                        f'"{tme}" too large! Use a cron interval string instead.',
                        param_hint="interval",
                    )

    return tme


def interval_option(f):
    return click.option(
        "-i",
        "--interval",
        default="10m",  # every 10 mins
        type=str,
        help="check interval: either an integer time in minutes *or* a cron string (e.g. `0 22 * * 1-5`)"
        " *or* a number postfixed by m|h|d indicating every (min/hour/day) e.g. 12h is every 12 hours",
        show_default=True,
    )(f)


@cli.command(
    epilog=click.style(
        'Use "crontab -l" to see if watch has been installed',
        fg="magenta",
    ),
)
@click.option(
    "-t",
    "--mem-threshold",
    default=100,
    help="memory min free space in megabytes",
    show_default=True,
)
@click.option(
    "-d",
    "--disk-threshold",
    default=100,
    help="disk partition min free space in megabytes",
    show_default=True,
)
@click.option(
    "-m",
    "--mailhost",
    help="SMTP mail host to connect to",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="send email whatever (only when --run is specified)",
)
@interval_option
@click.option("-r", "--run", is_flag=True, help="just run the command and exit")
@click.option("--test", "is_test", is_flag=True, help="show cron command only")
@click.argument("email", required=False)
@pass_config
def watch(
    cli: Cli,
    email: str | None,
    run: bool,
    mem_threshold: int,
    disk_threshold: int,
    mailhost: str | None,
    interval: str,
    force: bool,
    is_test: bool,
) -> None:
    """Install a crontab watch on low memory and diskspace [**requires psutil**]"""
    import sys
    from pathlib import Path
    from datetime import datetime
    from .utils import require_mod
    from .config import get_config

    require_mod("psutil")

    if run:
        if force:
            mem_threshold = -1
            disk_threshold = -1
        run_watch(email, mem_threshold, disk_threshold, mailhost)
        # write to watch.log
        click.echo(f"watch run at: {datetime.now()}")
        return

    if not email:
        raise click.BadArgumentUsage("need email address to send to")

    tme = make_cron_interval(interval)

    cfg = ""
    if cli.configfile is not None:
        cf = Path(cli.configfile).expanduser().absolute()
        cfg = f" -c {cf}"

    out = "1>watch.log 2>&1"
    mh = ""
    if mailhost is not None:
        mh = f" -m {mailhost}"
    C = (
        f"{tme} {sys.executable}"
        f" -m flask_nginx{cfg} watch{mh} --run -t {mem_threshold} -d {disk_threshold} {email} {out}"
    )
    if is_test:
        click.echo(C)
    else:
        add_cron_command(C, " -m flask_nginx .*watch")
        config = get_config()
        click.secho(
            f"will email to: {config.mailhost} from {config.sender}",
            fg="yellow",
        )


@cli.command(
    epilog=click.style(
        'Use "crontab -l" to see if watch has been installed',
        fg="magenta",
    ),
)
@interval_option
@click.option(
    "-f",
    "--footprint",
    "is_footprint",
    is_flag=True,
    help="is a footprint command",
)
@click.option(
    "-a",
    "--append",
    help="append output to a logfile. (--logfile takes precedence)",
    type=click.Path(dir_okay=False, file_okay=True),
)
@click.option(
    "-l",
    "--logfile",
    help="write output to a logfile (specified relative to the $HOME directory)",
    type=click.Path(dir_okay=False, file_okay=True),
)
@click.option("-t", "--test", "is_test", is_flag=True, help="show cron command only")
@click.argument("command", nargs=-1)
def cron(
    command: list[str],
    interval: str,
    is_test: bool,
    is_footprint: bool,
    logfile: str | None,
    append: str | None,
) -> None:
    """Install a python crontab command"""
    import os
    import sys

    if not command:
        return

    if is_footprint:
        command = ["-m", "flask_nginx", *command]

    cmd = " ".join(command)
    if not is_footprint and os.path.isfile(cmd):
        cmd = os.path.abspath(cmd)

    old = cmd
    tme = make_cron_interval(interval)
    if logfile is not None:
        out = f"1>{logfile} 2>&1"
    elif append is not None:
        out = f"1>>{append} 2>&1"
    else:
        out = "1>/dev/null 2>&1"
    C = f"{tme} {sys.executable} {cmd} {out}"
    if is_test:
        click.echo(C)
    else:
        add_cron_command(C, old)
