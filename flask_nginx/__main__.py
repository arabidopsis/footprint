# pylint: disable=unused-import
from __future__ import annotations

from . import irds  # noqa:
from . import mailer  # noqa:
from . import mysql  # noqa:
from . import remote  # noqa:
from . import restartd  # noqa:
from . import rsync  # noqa:
from . import watch  # noqa:
from .cli import cli
from .systemd import nginx  # noqa:
from .systemd import supervisor  # noqa:
from .systemd import systemd  # noqa:

# from . import logo  # noqa:

if __name__ == "__main__":
    cli.main(prog_name="footprint")
