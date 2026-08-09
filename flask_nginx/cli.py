from __future__ import annotations

from dataclasses import dataclass

import click


@dataclass
class Cli:
    configfile: str | None = None


pass_config = click.make_pass_decorator(Cli, ensure=True)


@click.group(
    epilog=click.style("Commands to manage websites\n", fg="magenta"),
)
@click.option(
    "-c",
    "--config",
    type=click.Path(file_okay=True, dir_okay=False, exists=True),
    help="configuration file for turnover [.TOML format]",
)
@click.version_option()
@click.pass_context
def cli(ctx: click.Context, config: str | None = None) -> None:
    ctx.obj = Cli(config)
    if config is not None:
        from .config import set_config_from_file

        set_config_from_file(config)


@cli.command()
def update() -> None:
    """Update this package from repository (latest commit!)."""
    import subprocess
    import sys
    from shutil import which

    from .config import REPO

    uv = which("uv")
    if uv:
        ret = subprocess.call([uv, "pip", "install", "-U", REPO])
    else:
        ret = subprocess.call([sys.executable, "-m", "pip", "install", "-U", REPO])
    if ret:
        click.secho(f"can't install {REPO}", fg="red")
        raise click.Abort()


@cli.command()
def repo() -> None:
    """Show git repository."""
    from .config import REPO

    click.echo(REPO)


@cli.command()
def config_show() -> None:
    """Show configuration."""
    from dataclasses import fields

    from .config import Config, get_config

    config = get_config()

    n = max(len(f.name) for f in fields(Config))

    for f in fields(Config):
        k = f.name
        v = getattr(config, f.name)
        click.echo(f"{k:<{n}}: {v}")


@cli.command()
@click.option("-a", "--append", is_flag=True, help="append to file")
@click.argument("filename")
def config_dump(filename: str, append: bool) -> None:
    """Dump configuration."""
    from .config import dump_to_file
    from .utils import require_mod

    require_mod("toml")

    if not dump_to_file(filename, append=append):
        click.secho("can't dump configuration!", fg="red", err=True)
        raise click.Abort()
