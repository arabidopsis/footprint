import getpass
import math
import os
import re
import typing as t
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from threading import Thread

from invoke import Context, Responder, Result

if t.TYPE_CHECKING:
    from sqlalchemy.engine import Engine  # pylint: disable=unused-import
    from sqlalchemy.engine.url import URL  # pylint: disable=unused-import

SUDO = t.Callable[..., Result]


def human(num: int, suffix: str = "B", scale: int = 1) -> str:
    if not num:
        return "0B"
    num *= scale
    magnitude = int(math.floor(math.log(abs(num), 1000)))
    val = num / math.pow(1000, magnitude)
    if magnitude > 7:
        return "{:.1f}{}{}".format(val, "Y", suffix)
    return "{:3.1f}{}{}".format(
        val, ("", "k", "M", "G", "T", "P", "E", "Z")[magnitude], suffix
    )


def rmfiles(files: t.List[str]) -> None:
    for f in files:
        with suppress(OSError):
            os.remove(f)


def get_pass(VAR: str, msg: str) -> str:
    if VAR not in os.environ:
        return getpass.getpass(f"{msg} password: ")
    return os.environ[VAR]


def getresponder(password: t.Optional[str], pattern: str, env: str) -> Responder:

    if password is None:
        password = os.environ.get(env)
    if password is None:
        password = getpass.getpass(pattern)

    return Responder(pattern=re.escape(pattern), response=password + "\n")


def multiline_comment(comment: str) -> t.List[str]:
    return [f"// {line}" for line in comment.splitlines()]


def flatten_toml(d: t.Dict[str, t.Any]) -> t.Dict[str, t.Any]:
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
    c: t.Optional[Context] = None, password: t.Optional[str] = None, lazy: bool = False
) -> SUDO:

    if c is None:
        c = Context()
    pattern = "Enter password:"
    resp = lambda: getresponder(password, pattern, "MYSQL_PASSWORD")
    supass = None if lazy else resp()

    def mysql(cmd, **kw):
        nonlocal supass
        if supass is None:
            supass = resp()
        kw.setdefault("pty", True)
        kw.setdefault("hide", True)
        return c.run(cmd, watchers=[supass], **kw)

    return mysql


def suresponder(
    c=t.Optional[Context], rootpw: t.Optional[str] = None, lazy: bool = False
) -> SUDO:

    if c is None:
        c = Context()

    pattern = "Password: "
    resp = lambda: getresponder(rootpw, pattern, "ROOT_PASSWORD")
    supass = None if lazy else resp()

    def sudo(cmd, **kw):
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


def sudoresponder(
    c=t.Optional[Context], sudopw: t.Optional[str] = None, lazy: bool = False
) -> SUDO:

    if c is None:
        c = Context()

    pattern = "[sudo] password: "
    resp = lambda: getresponder(sudopw, pattern, "SUDO_PASSWORD")

    supass = None if lazy else resp()

    def sudo(cmd, **kw):
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


def update_url(url_or_str: t.Union[str, "URL"], **kw) -> "URL":
    from sqlalchemy.engine.url import make_url

    # sqlalchemy 1.4 url is immutable
    if hasattr(url_or_str, "set"):
        return t.cast("URL", url_or_str).set(**kw)
    url = make_url(str(url_or_str))
    for k, v in kw.items():
        setattr(url, k, v)
    return url


@contextmanager
def connect_to(url: t.Union[str, "URL"]) -> "Engine":
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


def is_local(machine: t.Optional[str]) -> bool:
    return machine in {None, "127.0.0.1", "localhost"}


def make_connection(machine: t.Optional[str] = None):
    from fabric import Connection

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
