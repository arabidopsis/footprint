# pylint: disable=unused-import
from __future__ import annotations

from . import dbsize  # noqa:
from . import irds  # noqa:
from . import logo  # noqa:
from . import mailer  # noqa:
from . import mysqldump  # noqa:
from . import remote  # noqa:
from . import restartd  # noqa:
from . import rsync  # noqa:
from . import supervisor  # noqa:
from . import systemd  # noqa:
from . import typescript  # noqa:
from . import watch  # noqa:
from .cli import cli

if __name__ == "__main__":
    cli.main(prog_name="footprint")
