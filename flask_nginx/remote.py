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
def secret(size: int) -> None:
    """Generate secret keys for Flask apps"""
    from secrets import token_bytes

    print("SECRET_KEY =", token_bytes(size))
    print("SECURITY_PASSWORD_SALT =", token_bytes(size))
