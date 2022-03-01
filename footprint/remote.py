import click

from .cli import cli


@cli.command()
@click.option("-r", "--repo", default=".", help="repository location on local machine")
@click.option("-d", "--directory", default=".", help="location on remote machine")
@click.argument("machine")
def install_repo(machine: str, repo: str, directory: str) -> None:
    """Install a repo on a remote machine."""
    from fabric import Connection

    with Connection(machine) as c:
        if directory != ".":
            c.run(f'mkdir -p "{directory}"')
        with c.cd(directory):
            r = c.local(
                f"git -C {repo} config --get remote.origin.url", warn=True, hide=True
            ).stdout.strip()
            c.run(f"git clone {r}", pty=True)


@cli.command(
    epilog=click.style(
        """use e.g.: footprint secret >> instance/app.cfg""", fg="magenta"
    )
)
@click.option("--size", default=32, help="size of secret in bytes", show_default=True)
def secret(size: int):
    """Generate secret keys for Flask apps"""
    from secrets import token_bytes

    print("SECRET_KEY =", token_bytes(size))
    print("SECURITY_PASSWORD_SALT =", token_bytes(size))


@cli.command()
@click.option("--su", "asroot", is_flag=True, help="run as root")
@click.argument("src")
def du(src: str, asroot: bool) -> None:
    """find directory size."""
    from fabric import Connection

    from .utils import human, suresponder

    if ":" not in src:
        raise click.BadParameter("SRC must be {machine}:{directory}", param_hint="src")

    machine, directory = src.split(":", 1)
    with Connection(machine) as c:
        if asroot:
            run = suresponder(c)
        else:
            run = c.run
        o = run(f'du -sb "{directory}"', hide=True).stdout.strip()
        if asroot:
            o = o.replace("Password:", "").strip()
        size, _ = o.split()
        size = int(size)
    click.secho(f"{directory}: {human(size)}")
