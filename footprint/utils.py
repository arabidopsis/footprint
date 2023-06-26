from __future__ import annotations

import getpass
import math
import os
import re
from contextlib import contextmanager
from contextlib import suppress
from dataclasses import dataclass
from threading import Thread
from typing import Any
from typing import Callable
from typing import cast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabric import Connection  # pylint: disable=unused-import
    from invoke import Context, Responder, Result
    from sqlalchemy.engine import Engine  # pylint: disable=unused-import
    from sqlalchemy.engine.url import URL  # pylint: disable=unused-import

SUDO = Callable[..., "Result"]


def human(num: int, suffix: str = "B", scale: int = 1) -> str:
    if not num:
        return "0B"
    num *= scale
    magnitude = int(math.floor(math.log(abs(num), 1000)))
    val = num / math.pow(1000, magnitude)
    if magnitude > 7:
        return f"{val:.1f}Y{suffix}"
    mag = ("", "k", "M", "G", "T", "P", "E", "Z")[magnitude]
    return f"{val:3.1f}{mag}{suffix}"


def rmfiles(files: list[str]) -> None:
    for f in files:
        with suppress(OSError):
            os.remove(f)


def get_pass(VAR: str, msg: str) -> str:
    if VAR not in os.environ:
        return getpass.getpass(f"{msg} password: ")
    return os.environ[VAR]


def getresponder(password: str | None, pattern: str, env: str) -> Responder:
    from invoke import Responder

    if password is None:
        password = os.environ.get(env)
    if password is None:
        password = getpass.getpass(pattern)

    return Responder(pattern=re.escape(pattern), response=password + "\n")


def multiline_comment(comment: str) -> list[str]:
    return [f"// {line}" for line in comment.splitlines()]


def flatten_toml(d: dict[str, Any]) -> dict[str, Any]:
    def inner(d, view: str = "", level=0):
        for k, v in d.items():
            if "." in k:
                continue
            if isinstance(v, dict) and level == 0:
                yield from inner(v, f"{view}{k}.", level=level + 1)
            else:
                yield f"{view}{k}", v

    return dict(inner(d))


def gethomedir(user=""):
    return os.path.expanduser(f"~{user}")


def mysqlresponder(
    c: Context | None = None,
    password: str | None = None,
    lazy: bool = False,
) -> SUDO:
    from invoke import Context

    if c is None:
        c = Context()
    pattern = "Enter password:"

    def resp():
        return getresponder(password, pattern, "MYSQL_PASSWORD")

    supass = None if lazy else resp()

    def mysql(cmd: str, **kw) -> Result:
        assert c is not None
        nonlocal supass
        if supass is None:
            supass = resp()
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(cmd, watchers=[supass], **kw)

    return mysql


def suresponder(
    c: Context | None,
    rootpw: str | None = None,
    lazy: bool = False,
) -> SUDO:
    from invoke import Context

    from .config import ROOT_PASSWORD

    if c is None:
        c = Context()

    pattern = "Password: "

    def resp():
        return getresponder(rootpw, pattern, ROOT_PASSWORD)

    supass = None if lazy else resp()

    def sudo(cmd: str, **kw):
        assert c is not None
        nonlocal supass
        if supass is None:
            supass = resp()
        # https://www.gnu.org/software/bash/manual/html_node/Single-Quotes.html
        # cmd = cmd.replace("'", r"\'")
        cmd = cmd.replace('"', r"\"")
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(f'su -c "{cmd}"', watchers=[supass], **kw)

    return sudo


def toml_load(path: str) -> dict[str, Any]:
    try:
        import tomllib

        with open(path, "rb") as fp:
            return tomllib.load(fp)
    except ImportError:
        import toml

        return toml.load(path)


def init_config(application_dir: str = ".") -> None:
    from . import config

    project = os.path.join(application_dir, "pyproject.toml")
    if os.path.isfile(project):
        try:
            d = toml_load(project)
            cfg = d["tool"].get("footprint")
            if cfg:
                for k, v in cfg.items():
                    setattr(config, k.replace("-", "_").upper(), v)
        except ImportError:
            pass


def sudoresponder(
    c: Context | None,
    sudopw: str | None = None,
    lazy: bool = False,
) -> SUDO:
    from invoke import Context

    from .config import SUDO_PASSWORD

    if c is None:
        c = Context()

    pattern = "[sudo] password: "

    def resp():
        return getresponder(sudopw, pattern, SUDO_PASSWORD)

    supass = None if lazy else resp()

    def sudo(cmd: str, **kw) -> Result:
        assert c is not None
        nonlocal supass
        if supass is None:
            supass = resp()
        # https://www.gnu.org/software/bash/manual/html_node/Single-Quotes.html
        # cmd = cmd.replace("'", r"\'")
        cmd = cmd.replace('"', r"\"")
        kw.setdefault("pty", False)
        kw.setdefault("hide", True)
        cmd = f"sudo -S -p '{pattern}' {cmd}"
        return c.run(cmd, watchers=[supass], **kw)

    return sudo


def get_sudo(c: Context, use_su=False, lazy=True) -> SUDO:
    if os.getuid() == 0:  # we're running under sudo anyway!
        return c.run
    return sudoresponder(c, lazy=lazy) if not use_su else suresponder(c, lazy=lazy)


def update_url(url_or_str: str | URL, **kw) -> URL:
    from sqlalchemy.engine.url import make_url

    # sqlalchemy 1.4 url is immutable
    if hasattr(url_or_str, "set"):
        return cast("URL", url_or_str).set(**kw)
    url = make_url(str(url_or_str))
    for k, v in kw.items():
        setattr(url, k, v)
    return url


@contextmanager
def connect_to(url: str | URL, remote_port: int = 3306) -> Engine:
    from fabric import Connection
    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    from .config import RANDOM_PORT

    url = make_url(url)
    machine = url.host

    if not is_local(machine):
        url = update_url(url, host="127.0.0.1", port=RANDOM_PORT)

        with Connection(machine) as c:
            port = url.port or remote_port
            with c.forward_local(RANDOM_PORT, port):
                engine = create_engine(url)
                yield engine
    else:
        yield create_engine(url)


def browser(url: str = "http://127.0.0.1:2048", sleep: float = 2.0) -> Thread:
    import time
    import webbrowser

    def run():
        time.sleep(sleep)
        webbrowser.open_new_tab(url)

    tr = Thread(target=run)
    tr.start()
    return tr


@dataclass
class Runner:
    name: str
    cmd: str
    directory: str
    pty: bool = True
    warn: bool = True
    showcmd: bool = False

    def run(self) -> None:
        import click
        from invoke import Context

        click.secho(f"starting {self.name}", fg="yellow")
        if self.showcmd:
            click.echo(self.cmd)
        c = Context()

        with c.cd(self.directory):
            ret = c.run(
                self.cmd,
                pty=self.pty,  # seems to be need to see anything on the screen
                warn=self.warn,
            )
        click.secho(f"{self.name} server done", fg="green" if ret.ok else "red")

    def start(self, start=True) -> Thread:
        tr = Thread(target=self.run)
        if start:
            tr.start()
        return tr


def is_local(machine: str | None) -> bool:
    return machine in {None, "127.0.0.1", "localhost"}


def make_connection(machine: str | None = None) -> Context | Connection:
    from fabric import Connection
    from invoke import Context

    class IContext(Context):
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            pass

        def forward_local(self, *args, **kwargs):
            return self

    if not is_local(machine):
        return Connection(machine)
    return IContext()


@contextmanager
def maybe_closing(thing):
    try:
        yield thing
    finally:
        if hasattr(thing, "close"):
            thing.close()


def userdir():
    pth = os.environ.get("XDG_CONFIG_HOME")
    if pth:
        return os.path.join(pth, "systemd", "user")
    return os.path.expanduser("~/.config/systemd/user")
