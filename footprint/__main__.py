# pylint: disable=unused-import
from . import dbsize  # noqa:
from . import mysqldump  # noqa:
from . import remote  # noqa:
from . import rsync  # noqa:
from . import supervisor  # noqa:
from . import systemd  # noqa:
from . import typing  # noqa:
from .cli import cli
from .web import typed_flask  # noqa:

if __name__ == "__main__":
    cli.main(prog_name="footprint")
