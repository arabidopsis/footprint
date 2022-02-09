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

    v = "-v" if verbose else ""
    c = Context()

    if not src.endswith("/"):
        src += "/"
    if tgt.endswith("/"):
        tgt = tgt[:-1]
    cmd = f"""rsync -a {v} --delete {src} {tgt}"""
    c.run(cmd)


@cli.command(name="rsync")
@click.option("-v", "--verbose", is_flag=True)
@click.argument("src")
@click.argument("tgt")
def rsync_(src: str, tgt: str, verbose: bool):
    """Sync two directories on two possibly different machines."""
    rsync(src, tgt, verbose)
