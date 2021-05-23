import getpass
import math
import re
import os
from contextlib import contextmanager, suppress
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


def mysqlresponder(c=None, password: str = None):
    from invoke import Responder, Context

    if c is None:
        c = Context()
    pattern = "Enter password:"

    if password is None:
        pw = os.environ.get("MYSQL_PASSWORD")
    if password is None:
        password = getpass.getpass("*mysql* password")
    supass = Responder(pattern=re.escape(pattern), response=pw + "\n")

    def mysql(cmd, **kw):
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(cmd, watchers=[supass], **kw)

    return mysql


def suresponder(c=None, rootpw: str = None):
    from invoke import Responder, Context

    if c is None:
        c = Context()

    pattern = "Password: "

    if rootpw is None:
        rootpw = os.environ.get("ROOT_PASSWORD")
    if rootpw is None:
        rootpw = getpass.getpass("*root* password: ")
    supass = Responder(pattern=re.escape(pattern), response=rootpw + "\n")

    def sudo(cmd, **kw):
        # https://www.gnu.org/software/bash/manual/html_node/Single-Quotes.html
        # cmd = cmd.replace("'", r"\'")
        cmd = cmd.replace('"', r"\"")
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(f'su -c "{cmd}"', watchers=[supass], **kw)

    return sudo


def sudoresponder(c=None, password: str = None):
    from invoke import Responder, Context

    if c is None:
        c = Context()

    pattern = "[sudo] password: "

    if password is None:
        password = os.environ.get("SUDO_PASSWORD")
    if password is None:
        password = getpass.getpass(pattern)
    supass = Responder(pattern=re.escape(pattern), response=password + "\n")

    def sudo(cmd, **kw):
        # https://www.gnu.org/software/bash/manual/html_node/Single-Quotes.html
        # cmd = cmd.replace("'", r"\'")
        cmd = cmd.replace('"', r"\"")
        kw.setdefault("pty", False)
        kw.setdefault("hide", True)
        cmd = f"sudo -S -p '{pattern}' {cmd}"
        return c.run(cmd, watchers=[supass], **kw)

    return sudo


def update_url(url, **kw):
    from sqlalchemy.engine.url import make_url

    # sqlalchemy 1.4 url is immutable
    if hasattr(url, "set"):
        return url.set(**kw)
    url = make_url(str(url))
    for k, v in kw.items():
        setattr(url, k, v)
    return url


@contextmanager
def connect_to(url):
    from fabric import Connection
    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .config import RANDOM_PORT

    url = make_url(url)
    machine = url.host
    islocal = machine in {"127.0.0.1", "localhost"}
    if not islocal:
        url = update_url(url, host="127.0.0.1", port=RANDOM_PORT)

        with Connection(machine) as c:
            with c.forward_local(RANDOM_PORT, 3306):
                engine = create_engine(url)
                yield engine
    else:
        yield create_engine(url)
