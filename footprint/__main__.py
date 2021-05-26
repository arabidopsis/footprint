# pylint: disable=unused-import
from . import citations  # noqa:
from . import dbsize  # noqa:
from . import mysqldump  # noqa:
from . import remote  # noqa:
from . import rsync  # noqa:
from . import supervisor  # noqa:
from . import systemd  # noqa:
from .cli import cli

if __name__ == "__main__":
    cli.main(prog_name="footprint")
