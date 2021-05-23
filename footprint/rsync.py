import click

from .cli import cli


def mkdir(c, directory):
    from .utils import suresponder

    user = c.run("echo $USER", hide=True).stdout.strip()

    sudo = suresponder(c)
    sudo(f"mkdir -p '{directory}'")
    if user is not None:
        sudo(f"chown {user} {directory}")


def rsync(src, tgt, verbose=False):
    from fabric import Connection

    machine1, src = src.split(":", 1)
    if not src.endswith("/"):
        src += "/"
    if ":" in tgt:
        machine2, tgt = tgt.split(":", 1)
    else:
        machine2, tgt = tgt, src
    with Connection(machine2) as c:
        if c.run(f'test -d "{tgt}"', warn=True).failed:
            mkdir(c, tgt)
    v = "-v" if verbose else ""
    with Connection(machine1) as c:
        c.run(f"test -d {src}")
        cmd = f"""rsync -a {v} --delete {src} {machine2}:{tgt}"""
        c.run(cmd)


@cli.command(name="rsync")
@click.option("-v", "--verbose", is_flag=True)
@click.argument("src")
@click.argument("tgt")
def rsync_(src, tgt, verbose):
    """Sync two directories on two different machines."""
    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")
    rsync(src, tgt, verbose)
