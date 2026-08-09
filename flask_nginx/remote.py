from __future__ import annotations

import click

from .cli import cli


@cli.command(
    epilog=click.style(
        """use e.g.: footprint secret >> instance/app.cfg""",
        fg="magenta",
    ),
)
@click.option("--size", default=32, help="size of secret in bytes", show_default=True)
@click.option("--str", "as_str", is_flag=True, help="output as string")
def secret(size: int, *, as_str: bool) -> None:
    """Generate secret keys for Flask apps."""
    from secrets import token_bytes

    def t() -> str | bytes:
        b = token_bytes(size)
        if not as_str:
            return b
        return b.decode("latin1")

    click.echo(f"SECRET_KEY = {t()!r}")
    click.echo(f"SECURITY_PASSWORD_SALT = {t()!r}")
