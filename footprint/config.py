from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from dataclasses import replace

from .utils import toml_load

VERSION = "0.7.12"
REPO = "git+https://github.com/arabidopsis/footprint.git"


@dataclass
class ConfigClass:
    # mailhost: str= "uwa-edu-au.mail.protection.outlook.com"
    mailhost: str = "antivirus.uwa.edu.au"
    datastore: str = "//drive.irds.uwa.edu.au/sci-ms-001"
    # directories that *might* be in the static directory
    static_dir: str = (
        r"img|images|js|css|media|docs|tutorials|notebooks|downloads|\.well-known"
    )
    # basic files that have urls such as /robots.txt /favicon.ico etc.
    static_files: str = (
        r"robots\.txt|crossdomain\.xml|favicon\.ico|browserconfig\.xml|humans\.txt"
    )
    # exclude these filenames/directories from static consideration
    exclude: set[str] = field(default_factory=lambda: {"__pycache__"})
    # directory to put config files: (Ubuntu, RHEL8)
    nginx_dirs: tuple[str, ...] = ("/etc/nginx/sites-enabled", "/etc/nginx/conf.d")
    arg_color: str = "yellow"  # use "none" for no color


XConfig: ConfigClass | None = None


def get_config() -> ConfigClass:
    global XConfig
    if XConfig is None:
        XConfig = _init_config(ConfigClass())
    return XConfig


def _init_config(config: ConfigClass, application_dir: str = ".") -> ConfigClass:
    project = os.path.join(application_dir, "pyproject.toml")
    if os.path.isfile(project):
        try:
            d = toml_load(project)
            if "tool" not in d:
                return config
            cfg = d["tool"].get("footprint")
            data = {}
            for f in fields(config):
                if f.name in cfg:
                    data[f.name] = cfg[f.name]

            if data:
                config = replace(config, **data)

        except ImportError:
            pass
    return config
