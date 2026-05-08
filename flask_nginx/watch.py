from __future__ import annotations

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
        if test_line is None or test_line in line:
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


def make_cron_interval(interval_mins: int) -> str:
    if interval_mins >= 60:
        h = int(interval_mins // 60)
        tme = f"0 */{h} * * *"
    else:
        tme = f"*/{interval_mins} * * * *"
    return tme


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
    help="send email whatever",
)
@click.option(
    "-i",
    "--interval",
    default=10,
    help="check interval in minutes",
    show_default=True,
)
@click.option("-c", "--crontab", is_flag=True, help="install command into crontab")
@click.option("--test", "is_test", is_flag=True, help="show cron command only")
@click.argument("email", required=False)
@pass_config
def watch(
    cli: Cli,
    email: str,
    crontab: bool,
    mem_threshold: int,
    disk_threshold: int,
    mailhost: str | None,
    interval: int,
    force: bool,
    is_test: bool,
) -> None:
    """Install a crontab watch on low memory and diskspace [**requires psutil**]"""
    import sys
    from pathlib import Path
    from .utils import require_mod

    require_mod("psutil")

    if force and crontab:
        raise click.BadParameter("can't specifiy --force *and* --crontab")

    if not crontab:
        if force:
            mem_threshold = -1
            disk_threshold = -1
        run_watch(email, mem_threshold, disk_threshold, mailhost)
        return

    if not email:
        raise click.BadArgumentUsage("email must be present if --crontab specified")
    tme = make_cron_interval(interval)

    cfg = ""
    if cli.configfile is not None:
        cf = Path(cli.configfile).expanduser().absolute()
        cfg = f" -c {cf}"
    mh = ""
    if mailhost is not None:
        mh = f" -m {mailhost}"
    C = (
        f"{tme} {sys.executable}"
        f" -m flask_nginx{cfg} watch{mh} -t {mem_threshold} -d {disk_threshold} {email} 1>/dev/null 2>&1"
    )
    if is_test:
        click.echo(C)
    else:
        add_cron_command(C, "footprint watch")


@cli.command(
    epilog=click.style(
        'Use "crontab -l" to see if watch has been installed',
        fg="magenta",
    ),
)
@click.option(
    "-i",
    "--interval",
    default=10,
    help="check interval in minutes",
    show_default=True,
)
@click.option(
    "-f",
    "--footprint",
    "is_footprint",
    is_flag=True,
    help="is a footprint command",
)
@click.option("-t", "--test", "is_test", is_flag=True, help="show cron command only")
@click.argument("command", nargs=-1)
def cron(command: list[str], interval: int, is_test: bool, is_footprint: bool) -> None:
    """Install a python crontab command"""
    import os
    import sys

    if not command:
        return

    if is_footprint:
        command = ["-m", "flask_nginx", *command]

    cmd = " ".join(command)
    old = cmd
    tme = make_cron_interval(interval)
    if os.path.isfile(cmd):
        cmd = os.path.abspath(cmd)

    C = f"{tme} {sys.executable} {cmd} 1>/dev/null 2>&1"
    if is_test:
        click.echo(C)
    else:
        add_cron_command(C, old)
