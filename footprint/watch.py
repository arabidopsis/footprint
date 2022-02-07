import typing as t

import click

from .cli import cli


def vmemory_ok(threshold: int = 100) -> t.List[str]:
    import psutil

    from .utils import human

    m = psutil.virtual_memory()
    mn = threshold * 1024 * 1024  # megabytes
    mins = f" < {human(mn)}" if mn > 0 else ""
    if m.available < mn or mn <= 0:
        return [f"low memory: {human(m.available)}{mins} ({m.percent}%)"]
    return []


def disks_ok(threshold: int = 100) -> t.List[str]:
    import psutil

    from .utils import human

    mounts = [
        p.mountpoint
        for p in psutil.disk_partitions()
        if not p.device.startswith("/dev/loop")
    ]
    mn = threshold * 1024 * 1024  # megabytes
    mins = f" < {human(mn)}" if mn > 0 else ""

    ret = []
    for m in mounts:
        du = psutil.disk_usage(m)
        if du.free < mn or mn <= 0:
            ret.append(f"partition {m}: {human(du.free)}{mins} ({du.percent}%)")
    return ret


def watch_options(f):
    from .config import MAILHOST

    f = click.option(
        "-t",
        "--mem-threshold",
        default=100,
        help="memory threshold in megabytes",
        show_default=True,
    )(f)
    f = click.option(
        "-d",
        "--disk-threshold",
        default=100,
        help="disk partition threshold in megabytes",
        show_default=True,
    )(f)
    f = click.option(
        "-m",
        "--mailhost",
        default=MAILHOST,
        help="mail host to email",
        show_default=True,
    )(f)

    return f


@cli.command()
@watch_options
@click.argument("email", required=False)
def watch(
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


@cli.command()
@watch_options
@click.option(
    "-i", "--interval", default=10, help="check interval in minutes", show_default=True
)
@click.argument("email")
def crontab(
    email: str, mem_threshold: int, disk_threshold: int, mailhost: str, interval: int
):
    """Install a crontab watch on low memory and diskspace"""
    import sys
    from tempfile import NamedTemporaryFile

    from invoke import Context

    from .config import MAILHOST

    if mailhost == MAILHOST:
        m = ""
    else:
        m = f" -m {mailhost}"

    C = (
        f"*/{interval} * * * * {sys.executable}"
        f" -m footprint watch{m} -t {mem_threshold} -d {disk_threshold} {email} 1>/dev/null 2>&1"
    )
    c = Context()
    p = c.run("crontab -l", warn=True, hide=True).stdout
    ct = []
    for line in p.splitlines():
        if "footprint watch" in line:
            ct.append(C)
        else:
            ct.append(line)

    with NamedTemporaryFile("wt") as fp:
        fp.write("\n".join(ct))
        fp.write("\n")
        fp.flush()
        c.run(f"crontab {fp.name}")
