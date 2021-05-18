import getpass
import math
import os
from contextlib import suppress
from typing import List


def human(num: int, suffix="B", scale=1) -> str:
    if not num:
        return ""
    num *= scale
    magnitude = int(math.floor(math.log(abs(num), 1000)))
    val = num / math.pow(1000, magnitude)
    if magnitude > 7:
        return "{:.1f}{}{}".format(val, "Y", suffix)
    return "{:3.1f}{}{}".format(
        val, ["", "k", "M", "G", "T", "P", "E", "Z"][magnitude], suffix
    )


def rmfiles(files: List[str]):
    for f in files:
        with suppress(OSError):
            os.remove(f)


def get_pass(VAR: str, msg: str) -> str:
    if VAR not in os.environ:
        return getpass.getpass(f"{msg} password: ")
    return os.environ[VAR]


def suresponder(c, rootpw: str = None):
    from invoke import Responder

    if rootpw is None:
        rootpw = getpass.getpass(f"{c.host}: *root* password: ")
    supass = Responder(pattern="Password:", response=rootpw + "\n")

    def sudo(cmd, **kw):
        # https://www.gnu.org/software/bash/manual/html_node/Single-Quotes.html
        # cmd = cmd.replace("'", r"\'")
        cmd = cmd.replace('"', r"\"")
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(f'su -c "{cmd}"', watchers=[supass], **kw)

    return sudo
