from __future__ import annotations

import click


@click.group(
    # cls=DYMGroup,
    epilog=click.style("Commands to manage websites\n", fg="magenta"),
)
@click.version_option()
def cli() -> None:
    pass


@cli.command()
def update() -> None:
    """Update this package"""
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
    """show git repository"""

    from .config import REPO

    click.echo(REPO)


@cli.command()
def config_show() -> None:
    """Show configuration"""
    from dataclasses import fields
    from .config import get_config, Config

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
    """Dump configuration"""
    from .utils import require_mod
    from .config import dump_to_file

    require_mod("toml")

    if not dump_to_file(filename, append):
        click.secho("can't dump configuration!", fg="red", err=True)
        raise click.Abort()


# @cli.command()
# @click.option("-p", "--with-python", is_flag=True)
# @click.option("-c", "--compile", "use_pip_compile", is_flag=True)
# @click.argument("project_dir", required=False)
def poetry_to_reqs(
    project_dir: str,
    with_python: bool,
    use_pip_compile: bool = True,
) -> None:
    """Generate a requirements.txt file from pyproject.toml [**may require toml**]"""
    import os
    import subprocess
    from contextlib import suppress
    from .utils import toml_load

    pyproject = "pyproject.toml"
    if project_dir:
        pyproject = os.path.join(project_dir, pyproject)
    if not os.path.isfile(pyproject):
        raise click.BadArgumentUsage("no pyproject.toml file!")

    def fix(req: str) -> str:
        if req.startswith("^"):
            return f">={req[1:]}"
        return req

    reqs = "\n".join(
        f"{k}{fix(v)}"
        for k, v in sorted(
            toml_load(pyproject)["tool"]["poetry"]["dependencies"].items(),
        )
        if with_python or k != "python" and isinstance(v, str)
    )
    if use_pip_compile:
        try:
            with open("requirements.in", "w", encoding="utf-8") as fp:
                click.echo(reqs, file=fp)
            subprocess.check_call(["pip-compile"])
        finally:
            with suppress(OSError):
                os.remove("requirements.in")
    else:
        click.echo(reqs)
