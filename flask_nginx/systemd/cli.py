from __future__ import annotations

import click

from ..cli import cli


@cli.group(help=click.style("nginx/systemd configuration commands", fg="magenta"))
def config() -> None:
    pass
