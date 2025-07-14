from __future__ import annotations

from . import irds as irds
from . import mailer as mailer
from . import mysql as mysql
from . import remote as remote
from . import restartd as restartd
from . import rsync as rsync
from . import watch as watch
from .cli import cli
from .systemd import nginx as nginx
from .systemd import supervisor as supervisor
from .systemd import systemd as systemd


if __name__ == "__main__":
    cli.main(prog_name="footprint")
