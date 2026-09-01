from __future__ import annotations

from . import (
    cifs,
    mailer,
    mysql,
    remote,
    restartd,
    rsync,
    watch,
)
from .cli import cli
from .systemd import nginx, supervisor, systemd

__all__ = [
    "cifs",
    "cli",
    "mailer",
    "mysql",
    "nginx",
    "remote",
    "restartd",
    "rsync",
    "supervisor",
    "systemd",
    "watch",
]

if __name__ == "__main__":
    cli.main(prog_name="footprint")
