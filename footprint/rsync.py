import click
from invoke import Context

from .cli import cli


def mkdir(c: Context, directory: str) -> None:
    from .utils import suresponder

    user = c.run("echo $USER", hide=True).stdout.strip()

    sudo = suresponder(c)
    sudo(f"mkdir -p '{directory}'")
    if user is not None:
        sudo(f"chown {user} {directory}")


def rsync(src: str, tgt: str, verbose: bool = False) -> None:
    from fabric import Connection

    assert ":" in src, src

    machine_src, src = src.split(":", 1)
    if not src.endswith("/"):
        src += "/"
    if ":" in tgt:
        machine_tgt, tgt = tgt.split(":", 1)
        machine_tgt += ":"
    else:
        machine_tgt = ""

    v = "-v" if verbose else ""
    with Connection(machine_src) as c:
        c.run(f"test -d {src}")
        cmd = f"""rsync -a {v} --delete {src} {machine_tgt}{tgt}"""
        c.run(cmd)


@cli.command(name="rsync")
@click.option("-v", "--verbose", is_flag=True)
@click.argument("src")
@click.argument("tgt")
def rsync_(src: str, tgt: str, verbose: bool):
    """Sync two directories on two different machines."""
    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")
    rsync(src, tgt, verbose)
