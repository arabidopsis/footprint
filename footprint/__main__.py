# pylint: disable=unused-import
from . import dbsize, remote  # noqa:
from . import mysqldump
from .cli import cli

if __name__ == "__main__":
    cli.main(prog_name="footprint")
