from __future__ import annotations

import getpass
import math
import os
import subprocess
from contextlib import contextmanager
from contextlib import suppress
from dataclasses import dataclass
from shutil import which as shwitch
from threading import Thread
from typing import Any

import click
from jinja2 import Template


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


def get_variables(template: Template) -> set[str]:
    from jinja2 import meta

    if template.filename is None:
        return set()
    env = template.environment
    with open(template.filename, encoding="utf-8") as fp:
        ast = env.parse(fp.read())
    return meta.find_undeclared_variables(ast)


def which(cmd: str) -> str:
    ret = shwitch(cmd)
    if ret is None:
        click.secho(f"no command {cmd}!", fg="red", err=True)
        raise click.Abort()
    return ret
