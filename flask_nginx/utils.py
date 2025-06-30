from __future__ import annotations

import getpass
import math
import os
import subprocess
from contextlib import contextmanager
from contextlib import suppress
from dataclasses import dataclass
from os.path import abspath
from os.path import expanduser
from os.path import normpath
from shutil import which as shwitch
from threading import Thread
from typing import Any
from typing import Iterator
from typing import TYPE_CHECKING
from typing import TypeVar

import click

if TYPE_CHECKING:
    from jinja2 import Template


@dataclass
class StaticFolder:
    url: str | None
    folder: str
    rewrite: bool  # use nginx `rewrite {{url}}/(.*) /$1 break;``


def fixstatic(s: StaticFolder, prefix: str) -> StaticFolder:
    url = prefix + (s.url or "")
    if url and s.folder.endswith(url):
        path = s.folder[: -len(url)]
        return StaticFolder(url, path, False)
    return StaticFolder(url, s.folder, s.rewrite if not prefix else True)


def topath(path: str) -> str:
    return normpath(abspath(expanduser(path)))


def get_dot_env(fname: str) -> dict[str, str | None] | None:
    try:
        from dotenv import dotenv_values  # type: ignore

        return dotenv_values(fname)
    except ImportError:
        import click

        click.secho(
            '".flaskenv" file detected but no python-dotenv module found',
            fg="yellow",
            bold=True,
            err=True,
        )
        return None


def human(num: int, suffix: str = "B", scale: int = 1) -> str:
    if not num:
        return f"0{suffix}"
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


def multiline_comment(comment: str) -> list[str]:
    return [f"// {line}" for line in comment.splitlines()]


def flatten_toml(d: dict[str, Any]) -> dict[str, Any]:
    def inner(
        d: dict[str, Any],
        view: str = "",
        level: int = 0,
    ) -> Iterator[tuple[str, Any]]:
        for k, v in d.items():
            if "." in k:
                continue
            if isinstance(v, dict) and level == 0:
                yield from inner(v, f"{view}{k}.", level=level + 1)  # type: ignore
            else:
                yield f"{view}{k}", v

    return dict(inner(d))


def gethomedir(user: str = "") -> str:
    user = user.replace("\\\\", "\\")
    return os.path.expanduser(f"~{user}")


def toml_load(path: str) -> dict[str, Any]:
    try:
        import tomllib  # type: ignore

        with open(path, "rb") as fp:
            return tomllib.load(fp)
    except ImportError:
        import toml  # type: ignore

        return toml.load(path)


def browser(url: str = "http://127.0.0.1:2048", sleep: float = 2.0) -> Thread:
    import time
    import webbrowser

    def run() -> None:
        time.sleep(sleep)
        webbrowser.open_new_tab(url)

    tr = Thread(target=run)
    tr.start()
    return tr


@dataclass
class Runner:
    name: str
    cmd: list[str]
    directory: str
    warn: bool = True
    showcmd: bool = False
    env: dict[str, str] | None = None
    shell: bool = False

    def run(self) -> subprocess.Popen[bytes]:
        click.secho(f"starting {self.name}", fg="yellow")
        if self.showcmd:
            click.echo(" ".join(str(s) for s in self.cmd))
        ret = subprocess.Popen(
            self.cmd,
            cwd=self.directory,
            env=self.getenv(),
            shell=self.shell,
        )
        return ret

    def getenv(self) -> dict[str, str] | None:
        if not self.env:
            return None
        return {**os.environ, **self.env}

    def start(self) -> subprocess.Popen[bytes]:
        return self.run()


def is_local(machine: str | None) -> bool:
    return machine in {None, "127.0.0.1", "localhost"}


T = TypeVar("T")


@contextmanager
def maybe_closing(thing: T) -> Iterator[T]:
    try:
        yield thing
    finally:
        if hasattr(thing, "close"):
            thing.close()  # type: ignore


def userdir() -> str:
    pth = os.environ.get("XDG_CONFIG_HOME")
    if pth:
        return os.path.join(pth, "systemd", "user")
    return os.path.expanduser("~/.config/systemd/user")


def get_variables(template: Template) -> set[str]:
    from jinja2 import meta

    if template.filename is None or template.filename == "<template>":
        return set()
    env = template.environment
    with open(template.filename, encoding="utf-8") as fp:
        ast = env.parse(fp.read())
    return meta.find_undeclared_variables(ast)


def which(cmd: str) -> str:
    ret = shwitch(cmd)
    if ret is None:
        click.secho(f"no executable {cmd}!", fg="red", err=True)
        raise click.Abort()
    return ret


def require_mod(mod: str, mod_name: str | None = None) -> None:
    from importlib import import_module

    try:
        import_module(mod)
        return
    except ModuleNotFoundError as e:
        import sys

        if mod_name is None:
            mod_name = mod
        click.secho(
            f"Please install {mod} ({sys.executable} -m pip install {mod_name})",
            fg="yellow",
            bold=True,
            err=True,
        )
        raise click.Abort() from e
