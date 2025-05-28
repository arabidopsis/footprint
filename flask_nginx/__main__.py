# pylint: disable=unused-import
from __future__ import annotations

from . import irds
from . import mailer
from . import mysql
from . import remote
from . import restartd
from . import rsync
from . import watch
from .cli import cli
from .systemd import nginx
from .systemd import supervisor
from .systemd import systemd

__all__ = [
    "irds",
    "cli",
    "mailer",
    "mysql",
    "remote",
    "restartd",
    "rsync",
    "watch",
    "nginx",
    "supervisor",
    "systemd",
]


if __name__ == "__main__":
    cli.main(prog_name="footprint")
