import typing as t

import click

from .cli import cli
from .config import MAILHOST


def vmemory_ok(threshold: int = 100) -> t.List[str]:
    import psutil

    from .utils import human

    m = psutil.virtual_memory()
    mn = threshold * 1024 * 1024  # megabyte
    if mn <= 0:
        return [f"memory available: {human(m.available)} ({m.percent}% used)"]

    if m.available < mn:
        return [f"low memory: {human(m.available)} < {human(mn)} ({m.percent}% used)"]
    return []


def disks_ok(threshold: int = 100) -> t.List[str]:
    import psutil

    from .utils import human

    mounts = [
        p.mountpoint
        for p in psutil.disk_partitions()
        if not p.device.startswith("/dev/loop") and not p.mountpoint.startswith("/boot")
    ]
    mn = threshold * 1024 * 1024  # megabytes

    ret: t.List[str] = []
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
    email: t.Optional[str], mem_threshold: int, disk_threshold: int, mailhost: str
):
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


def add_cron_command(cmd: str, test_line: t.Optional[str] = None) -> None:
    from tempfile import NamedTemporaryFile

    from invoke import Context

    c = Context()
    # find current crontab
    p = c.run("crontab -l", warn=True, hide=True).stdout
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
        c.run(f"crontab {fp.name}")


@cli.command(
    epilog=click.style(
        'Use "crontab -l" to see if watch has been installed', fg="magenta"
    )
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
    default=MAILHOST,
    help="SMTP mail host to connect to",
    show_default=True,
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="send email whatever",
)
@click.option(
    "-i", "--interval", default=10, help="check interval in minutes", show_default=True
)
@click.option("-c", "--crontab", is_flag=True, help="install command into crontab")
@click.option("-t", "--test", "is_test", is_flag=True, help="show cron command only")
@click.argument("email", required=False)
def watch(
    email: str,
    crontab: bool,
    mem_threshold: int,
    disk_threshold: int,
    mailhost: str,
    interval: int,
    force: bool,
    is_test: bool,
):
    """Install a crontab watch on low memory and diskspace"""
    import sys

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

    if mailhost == MAILHOST:
        m = ""
    else:
        m = f" -m {mailhost}"

    if interval >= 60:
        h = int(interval // 60)
        tme = f"0 */{h}"
    else:
        tme = f"*/{interval} *"

    C = (
        f"{tme} * * * {sys.executable}"
        f" -m footprint watch{m} -t {mem_threshold} -d {disk_threshold} {email} 1>/dev/null 2>&1"
    )
    if is_test:
        click.echo(C)
    else:
        add_cron_command(C, "footprint watch")


@cli.command(
    epilog=click.style(
        'Use "crontab -l" to see if watch has been installed', fg="magenta"
    )
)
@click.option(
    "-i", "--interval", default=10, help="check interval in minutes", show_default=True
)
@click.option("-t", "--test", "is_test", is_flag=True, help="show cron command only")
@click.argument("command")
def cron(command: str, interval: int, is_test: bool):
    """Install a crontab command"""
    import os
    import sys

    if interval >= 60:
        h = int(interval // 60)
        tme = f"0 */{h}"
    else:
        tme = f"*/{interval} *"
    if os.path.isfile(command):
        command = os.path.abspath(command)

    C = f"{tme} * * * {sys.executable} {command} 1>/dev/null 2>&1"
    if is_test:
        click.echo(C)
    else:
        add_cron_command(C)
