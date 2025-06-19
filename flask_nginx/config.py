from __future__ import annotations

import os
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from dataclasses import replace
from typing import IO

from .utils import toml_load


REPO = "git+https://github.com/arabidopsis/footprint.git"


@dataclass
class Config:
    mailhost: str = "uwa-edu-au.mail.protection.outlook.com"
    # mailhost: str = "antivirus.uwa.edu.au"
    datastore: str = "//drive.irds.uwa.edu.au/sci-ms-001"
    # directories that *might* be in the static directory
    static_dir: list[str] = field(
        default_factory=lambda: [
            "img",
            "images",
            "js",
            "css",
            "media",
            "docs",
            "tutorials",
            "notebooks",
            "downloads",
            ".well-known",
        ],
    )
    # basic files that have urls such as /robots.txt /favicon.ico etc.
    static_files: list[str] = field(
        default_factory=lambda: [
            "robots.txt",
            "crossdomain.xml",
            "favicon.ico",
            "browserconfig.xml",
            "humans.txt",
        ],
    )
    # exclude these filenames/directories from static consideration
    exclude: list[str] = field(default_factory=lambda: ["__pycache__"])
    # directory to put config files: (Ubuntu, RHEL8)
    nginx_dirs: list[str] = field(
        default_factory=lambda: ["/etc/nginx/sites-enabled", "/etc/nginx/conf.d"],
    )
    arg_color: str = "yellow"  # use "none" for no color


XConfig: Config | None = None


def get_config() -> Config:
    global XConfig
    if XConfig is None:
        XConfig = _init_config(Config())
    return XConfig


def _init_config(config: Config, application_dir: str = ".") -> Config:
    project = os.path.join(application_dir, "pyproject.toml")
    if os.path.isfile(project):
        try:
            d = toml_load(project)
            if "tool" not in d:
                return config
            cfg = d["tool"].get("footprint")
            if cfg is None:
                return config
            data = {}
            for f in fields(config):
                if f.name in cfg:
                    data[f.name] = cfg[f.name]

            if data:
                config = replace(config, **data)

        except ImportError:
            pass
        except Exception:
            import click

            click.secho(f'can\'t load "{project}"', fg="red", bold=True, err=True)
    return config


def dump_toml(config: Config, out: IO[str]) -> bool:
    try:
        import toml  # type: ignore

        d = dict(tool=dict(footprint=asdict(config)))
        toml.dump(d, out)
        return True
    except Exception:
        return False


def dump_to_file(filename: str, append: bool) -> bool:
    config = get_config()
    if filename == "-":
        import sys

        return dump_toml(config, sys.stdout)
    with open(filename, "a" if append else "w", encoding="utf-8") as fp:
        return dump_toml(config, fp)
