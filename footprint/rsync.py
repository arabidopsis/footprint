import click
from fabric import Connection

from .cli import cli


def rsync(src, tgt):
    machine1, src = src.split(":", 1)
    if ":" in tgt:
        machine2, tgt = tgt.split(":", 1)
    else:
        machine2, tgt = tgt, src

    with Connection(machine1) as c:
        c.run(f"test -d {src}")
        cmd = f"""rsync -a --delete {src} {machine2}:{tgt}"""
        c.run(cmd)


@cli.command(name="rsync")
@click.argument("src")
@click.argument("tgt")
def rsync_(src, tgt):
    """Sync two directories on two different machines."""
    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")
    rsync(src, tgt)
